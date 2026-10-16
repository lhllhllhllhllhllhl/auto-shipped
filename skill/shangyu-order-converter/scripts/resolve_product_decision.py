#!/usr/bin/env python3
"""Resolve one user product correction without mutating source order fields."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from convert_order import _find_runtime, _resolve_preferred_catalogs
from runtime_common import resolve_project_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把用户对商品候选的回答作为目标线索解析"
    )
    parser.add_argument("--decision-bundle", required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--target-hint", required=True)
    parser.add_argument(
        "--hint-type",
        choices=["auto", "spec_code", "product_code", "barcode", "product_name"],
        default="auto",
    )
    parser.add_argument(
        "--reuse-scope", choices=["current_batch", "future"], default="current_batch"
    )
    parser.add_argument(
        "--mapping-scope", choices=["source", "global"], default="source"
    )
    parser.add_argument("--catalog")
    parser.add_argument("--preferred-catalog", action="append", default=[])
    parser.add_argument("--mappings")
    parser.add_argument("--output")
    parser.add_argument("--project-root")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        print('{"status":"failed","code":"RUNTIME_MISSING"}')
        return 2
    runtime = _find_runtime(project_root)
    if runtime is None:
        print('{"status":"failed","code":"PYTHON_RUNTIME_MISSING"}')
        return 2
    registry_path = project_root / "config/catalog/current_product_catalog.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        catalog = Path(args.catalog or registry.get("path") or "").expanduser()
        if not catalog.is_absolute():
            catalog = project_root / catalog
        preferred = (
            [Path(value).expanduser().resolve() for value in args.preferred_catalog]
            if args.preferred_catalog
            else list(_resolve_preferred_catalogs(project_root))
        )
    except (OSError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(
            json.dumps(
                {"status": "failed", "code": "CATALOG_UNAVAILABLE", "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    if not catalog.is_file():
        print('{"status":"failed","code":"CATALOG_FILE_MISSING"}')
        return 2
    mappings = Path(
        args.mappings
        or project_root / "config/catalog/external_sku_mappings.json"
    ).expanduser().resolve()
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "resolve-product-decision",
        "--decision-bundle",
        str(Path(args.decision_bundle).expanduser().resolve()),
        "--decision-id",
        args.decision_id,
        "--target-hint",
        args.target_hint,
        "--hint-type",
        args.hint_type,
        "--reuse-scope",
        args.reuse_scope,
        "--mapping-scope",
        args.mapping_scope,
        "--catalog",
        str(catalog.resolve()),
        "--mappings",
        str(mappings),
    ]
    for path in preferred:
        command.extend(["--preferred-catalog", str(path)])
    if args.output:
        command.extend(["--output", str(Path(args.output).expanduser().resolve())])
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
