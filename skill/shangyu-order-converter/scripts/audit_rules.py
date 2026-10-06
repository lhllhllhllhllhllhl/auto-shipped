#!/usr/bin/env python3
"""Audit SOP rule coverage without reading or changing order data."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from convert_order import _find_runtime
from runtime_common import resolve_project_root


def main() -> int:
    parser = argparse.ArgumentParser(description="检查尚舆SOP业务规则覆盖状态")
    parser.add_argument(
        "--phase",
        choices=["conversion", "upload", "post_upload", "post_fulfillment", "manual_entry", "all"],
        default="all",
    )
    parser.add_argument("--scope")
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
        "audit-rules",
        "--phase",
        args.phase,
    ]
    if args.scope:
        command.extend(["--scope", args.scope])
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join([source_root, existing])
    )
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
