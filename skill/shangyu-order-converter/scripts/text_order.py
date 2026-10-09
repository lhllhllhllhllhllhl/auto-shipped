#!/usr/bin/env python3
"""Stable Skill entrypoint for Agent-extracted text or screenshot orders."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from convert_order import _find_runtime, _json_error
from runtime_common import resolve_project_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="校验文字/截图订单草稿，并在用户确认后生成标准化转换输入"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--draft", required=True)
    validate.add_argument("--project-root")
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--draft", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--order-number-state-dir")
    prepare.add_argument("--project-root")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        _json_error("找不到 Skill 内置或外部订单转换运行时。")
        return 1
    runtime = _find_runtime(project_root)
    if runtime is None:
        _json_error("找不到 Python 3.11+ 且包含 openpyxl 的运行环境。")
        return 1
    draft = Path(args.draft).expanduser().resolve()
    if not draft.is_file():
        _json_error(f"找不到文字订单草稿：{draft}")
        return 1

    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        (
            "validate-text-order-draft"
            if args.command == "validate"
            else "prepare-text-order-draft"
        ),
        "--draft",
        str(draft),
    ]
    if args.command == "prepare":
        command.extend(
            ["--output-dir", str(Path(args.output_dir).expanduser().resolve())]
        )
        if args.order_number_state_dir:
            command.extend(
                [
                    "--order-number-state-dir",
                    str(Path(args.order_number_state_dir).expanduser().resolve()),
                ]
            )
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join([source_root, existing])
    )
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
