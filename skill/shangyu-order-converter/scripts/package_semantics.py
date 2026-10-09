#!/usr/bin/env python3
"""Save, list, or export reusable package-semantics proposals."""

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理可回收的包装含义待登记提案")
    parser.add_argument("--project-root")
    parser.add_argument(
        "--state-dir",
        default=str(STATE_ROOT / "state" / "package-semantics" / "pending"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    save = subparsers.add_parser("save", help="保存用户确认以后沿用的包装含义")
    save.add_argument("--company-id", required=True)
    save.add_argument("--source-profile", required=True)
    save.add_argument("--product-family", required=True)
    save.add_argument("--source-expression", required=True)
    save.add_argument(
        "--semantic-type",
        required=True,
        choices=[
            "single_stick_packaged",
            "eight_stick_family_pack",
            "pack_content_count",
            "bundle",
        ],
    )
    save.add_argument(
        "--quantity-strategy",
        required=True,
        choices=[
            "same_as_source",
            "divide_by_sticks_per_target_unit",
            "requires_bundle_expansion",
        ],
    )
    save.add_argument("--sticks-per-target-unit", type=int)
    save.add_argument("--confirmation-token", required=True)

    subparsers.add_parser("list", help="列出当前Agent本机待登记提案")
    export = subparsers.add_parser("export", help="导出可直接发给主系统所有者的JSON")
    export.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        print(json.dumps({"status": "failed", "message": "找不到订单转换运行时。"}, ensure_ascii=False))
        return 1
    runtime = _find_runtime(project_root)
    if runtime is None:
        print(json.dumps({"status": "failed", "message": "找不到可用Python运行时。"}, ensure_ascii=False))
        return 1

    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        {
            "save": "save-package-semantics-proposal",
            "list": "list-package-semantics-proposals",
            "export": "export-package-semantics-proposals",
        }[args.command],
        "--state-dir",
        str(Path(args.state_dir).expanduser().resolve()),
    ]
    if args.command == "save":
        command.extend(
            [
                "--company-id",
                args.company_id,
                "--source-profile",
                args.source_profile,
                "--product-family",
                args.product_family,
                "--source-expression",
                args.source_expression,
                "--semantic-type",
                args.semantic_type,
                "--quantity-strategy",
                args.quantity_strategy,
                "--confirmation-token",
                args.confirmation_token,
            ]
        )
        if args.sticks_per_target_unit is not None:
            command.extend(["--sticks-per-target-unit", str(args.sticks_per_target_unit)])
    elif args.command == "export":
        command.extend(["--output", str(Path(args.output).expanduser().resolve())])

    environment = os.environ.copy()
    source_root = str(project_root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
