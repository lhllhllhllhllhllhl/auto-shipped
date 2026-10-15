#!/usr/bin/env python3
"""Save a source-file-bound replacement for one rejected official mapping."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from convert_order import _find_runtime, _resolve_catalog, _resolve_preferred_catalogs
from runtime_common import STATE_ROOT, resolve_project_root


def _source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="保存一条只对当前来源文件生效的正式映射临时替换"
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--source-profile", required=True)
    parser.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    parser.add_argument("--source-value", required=True)
    parser.add_argument("--source-spec", default="")
    parser.add_argument("--expected-product-code", required=True)
    parser.add_argument("--expected-spec-code", default="")
    parser.add_argument("--replacement-product-code", required=True)
    parser.add_argument("--replacement-spec-code", default="")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--confirmation-token", required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--preferred-catalog", action="append", default=[])
    parser.add_argument("--mappings")
    parser.add_argument("--official-mapping-snapshot")
    parser.add_argument("--output")
    parser.add_argument("--project-root")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        print('{"status":"failed","message":"找不到订单转换运行时。"}')
        return 1
    runtime = _find_runtime(project_root)
    catalog = _resolve_catalog(project_root, args.catalog)
    source = Path(args.source).expanduser().resolve()
    official = Path(
        args.official_mapping_snapshot
        or STATE_ROOT / "config" / "feishu-official-product-mappings.json"
    ).expanduser().resolve()
    output = Path(
        args.output
        or STATE_ROOT / "config" / "current-batch-mapping-replacements.json"
    ).expanduser().resolve()
    try:
        preferred_catalogs = _resolve_preferred_catalogs(
            project_root, tuple(args.preferred_catalog)
        )
    except FileNotFoundError as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False))
        return 1
    if runtime is None or catalog is None or not catalog.is_file():
        print('{"status":"failed","message":"运行环境或商品资料不可用。"}')
        return 1
    if not source.is_file():
        print('{"status":"failed","message":"找不到当前批次来源文件。"}')
        return 1
    if not official.is_file():
        print('{"status":"failed","message":"找不到已校验的飞书正式映射快照。"}')
        return 1
    mappings = Path(
        args.mappings
        or project_root / "config/catalog/external_sku_mappings.json"
    ).expanduser().resolve()
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "save-batch-mapping-replacement",
        "--catalog",
        str(catalog),
        "--mappings",
        str(mappings),
        "--mapping-overlay",
        str(official),
        "--output",
        str(output),
        "--company-id",
        args.company_id,
        "--source-profile",
        args.source_profile,
        "--identifier-type",
        args.identifier_type,
        "--source-value",
        args.source_value,
        "--source-spec",
        args.source_spec,
        "--expected-product-code",
        args.expected_product_code,
        "--expected-spec-code",
        args.expected_spec_code,
        "--replacement-product-code",
        args.replacement_product_code,
        "--replacement-spec-code",
        args.replacement_spec_code,
        "--source-fingerprint",
        _source_fingerprint(source),
        "--reason",
        args.reason,
        "--confirmed-by",
        args.confirmed_by,
        "--confirmation-token",
        args.confirmation_token,
    ]
    for preferred in preferred_catalogs:
        command.extend(["--preferred-catalog", str(preferred)])
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not current else os.pathsep.join([source_root, current])
    )
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
