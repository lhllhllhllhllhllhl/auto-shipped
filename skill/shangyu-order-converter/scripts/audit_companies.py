#!/usr/bin/env python3
"""Audit company registry references without reading or changing order data."""

from __future__ import annotations

import argparse
import os
import subprocess

from convert_order import _find_runtime
from runtime_common import resolve_project_root


def main() -> int:
    parser = argparse.ArgumentParser(description="检查尚舆公司库与模块引用")
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

    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join([source_root, existing])
    )
    return subprocess.run(
        [runtime, "-m", "auto_shipped.cli", "audit-companies"],
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
