#!/usr/bin/env python3
"""Save one explicitly confirmed current-batch item expansion overlay."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from convert_order import _find_runtime, _resolve_catalog, _resolve_preferred_catalogs
from runtime_common import resolve_project_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="保存仅当前来源文件生效的组合商品规则")
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--source-profile", required=True)
    parser.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    parser.add_argument("--source-value", required=True)
    parser.add_argument("--source-spec", default="")
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--components-json", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--preferred-catalog", action="append", default=[])
    parser.add_argument("--mappings")
    parser.add_argument("--project-root")
    parser.add_argument("--confirmation-token", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        print('{"status":"failed","code":"RUNTIME_MISSING"}')
        return 2
    runtime = _find_runtime(project_root)
    catalog = _resolve_catalog(project_root, args.catalog)
    try:
        preferred_catalogs = _resolve_preferred_catalogs(
            project_root, tuple(args.preferred_catalog)
        )
    except FileNotFoundError as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False))
        return 2
    if runtime is None or catalog is None or not catalog.is_file():
        print('{"status":"failed","code":"CATALOG_OR_RUNTIME_MISSING"}')
        return 2
    mappings = Path(
        args.mappings
        or project_root / "config/catalog/external_sku_mappings.json"
    ).expanduser().resolve()
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "save-batch-item-expansion",
        "--catalog",
        str(catalog),
        "--mappings",
        str(mappings),
        "--output",
        str(Path(args.output).expanduser().resolve()),
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
        "--source-fingerprint",
        args.source_fingerprint,
        "--components-json",
        args.components_json,
        "--evidence",
        args.evidence,
        "--confirmation-token",
        args.confirmation_token,
    ]
    for preferred in preferred_catalogs:
        command.extend(["--preferred-catalog", str(preferred)])
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join([source_root, existing])
    )
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
