#!/usr/bin/env python3
"""Build a deterministic pending mapping proposal without writing Feishu."""

from __future__ import annotations

import argparse
import os
import subprocess

from convert_order import _find_runtime
from runtime_common import resolve_project_root


def main() -> int:
    parser = argparse.ArgumentParser(description="生成飞书待确认商品映射提案")
    parser.add_argument("--submitted-by", required=True)
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--source-profile", required=True)
    parser.add_argument(
        "--identifier-type",
        required=True,
        choices=["product_code", "barcode", "product_name"],
    )
    parser.add_argument("--source-value", required=True)
    parser.add_argument("--source-spec", default="")
    parser.add_argument("--target-platform", default="guanyi")
    parser.add_argument("--product-code", required=True)
    parser.add_argument("--spec-code", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--official-revision", required=True)
    parser.add_argument("--operation-id")
    parser.add_argument("--project-root")
    args = parser.parse_args()

    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        print('{"status":"invalid","code":"RUNTIME_MISSING"}')
        return 2
    runtime = _find_runtime(project_root)
    if runtime is None:
        print('{"status":"invalid","code":"PYTHON_RUNTIME_MISSING"}')
        return 2

    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        "build-mapping-proposal",
        "--submitted-by",
        args.submitted_by,
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
        "--target-platform",
        args.target_platform,
        "--product-code",
        args.product_code,
        "--spec-code",
        args.spec_code,
        "--evidence",
        args.evidence,
        "--official-revision",
        args.official_revision,
    ]
    if args.operation_id:
        command.extend(["--operation-id", args.operation_id])

    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join([source_root, existing])
    )
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
