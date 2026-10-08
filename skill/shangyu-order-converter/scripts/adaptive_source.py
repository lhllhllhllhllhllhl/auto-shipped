#!/usr/bin/env python3
"""Portable entrypoint for privacy-safe adaptive Excel structure analysis."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from convert_order import _find_runtime
from runtime_common import resolve_project_root


def _error(message: str) -> int:
    print(
        json.dumps(
            {"status": "failed", "code": "ADAPTIVE_SOURCE_COMMAND_INVALID", "message": message},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in {"inspect", "propose"}:
        return _error(
            "用法：adaptive_source.py inspect --source <Excel>；或propose --source <Excel> --company <company_id> --output <plan.json>"
        )
    project_root = resolve_project_root()
    if project_root is None:
        return _error("找不到Skill运行时。")
    runtime = _find_runtime(project_root)
    if runtime is None:
        return _error("找不到Python 3.11+且包含openpyxl的运行环境。")
    command_name = (
        "inspect-source-structure"
        if sys.argv[1] == "inspect"
        else "propose-adaptive-plan"
    )
    command = [runtime, "-m", "auto_shipped.cli", command_name, *sys.argv[2:]]
    environment = os.environ.copy()
    source_root = str(Path(project_root) / "src")
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not current else os.pathsep.join([source_root, current])
    )
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
