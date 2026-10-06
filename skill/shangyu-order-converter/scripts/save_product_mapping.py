#!/usr/bin/env python3
"""Persist one explicitly confirmed product mapping into the local overlay."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from runtime_common import STATE_ROOT, resolve_project_root, state_python_candidates


def _find_runtime(project_root: Path) -> str | None:
    for candidate in [*state_python_candidates(project_root), sys.executable]:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        executable = str(path) if path.is_absolute() and path.is_file() else shutil.which(candidate)
        if executable:
            return executable
    return None


def _resolve_catalog(project_root: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    try:
        registry = json.loads(
            (project_root / "config/catalog/current_product_catalog.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return None
    value = registry.get("path")
    if not value:
        return None
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project_root / path).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="保存一条用户明确确认的商品映射")
    parser.add_argument("--source-profile", required=True)
    parser.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    parser.add_argument("--source-value", required=True)
    parser.add_argument("--source-spec", default="")
    parser.add_argument("--product-code", required=True)
    parser.add_argument("--spec-code", required=True)
    parser.add_argument("--reason", default="用户确认")
    parser.add_argument("--catalog")
    parser.add_argument("--mappings")
    parser.add_argument("--overlay")
    parser.add_argument("--project-root")
    parser.add_argument("--confirmation-token", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        print(json.dumps({"status": "failed", "message": "找不到订单转换运行时。"}, ensure_ascii=False))
        return 1
    runtime = _find_runtime(project_root)
    catalog = _resolve_catalog(project_root, args.catalog)
    if runtime is None or catalog is None or not catalog.is_file():
        print(json.dumps({"status": "failed", "message": "运行环境或商品资料不可用。"}, ensure_ascii=False))
        return 1
    mappings = Path(
        args.mappings
        or project_root / "config/catalog/external_sku_mappings.json"
    ).expanduser().resolve()
    overlay = Path(
        args.overlay
        or STATE_ROOT / "config" / "product-mapping-overrides.json"
    ).expanduser().resolve()
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "save-product-mapping",
        "--catalog",
        str(catalog),
        "--mappings",
        str(mappings),
        "--overlay",
        str(overlay),
        "--source-profile",
        args.source_profile,
        "--identifier-type",
        args.identifier_type,
        "--source-value",
        args.source_value,
        "--source-spec",
        args.source_spec,
        "--product-code",
        args.product_code,
        "--spec-code",
        args.spec_code,
        "--reason",
        args.reason,
        "--confirmation-token",
        args.confirmation_token,
    ]
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
