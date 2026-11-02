#!/usr/bin/env python3
"""Verify the converter-owned receipt before an Agent delivers an order workbook."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from convert_order import _find_runtime
from runtime_common import BUNDLE_MANIFEST, resolve_project_root


def main() -> int:
    parser = argparse.ArgumentParser(description="校验尚舆订单转换交付凭证")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--project-root")
    args = parser.parse_args()

    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        print(json.dumps({"status": "failed", "message": "找不到订单转换运行时。"}, ensure_ascii=False))
        return 1
    runtime = _find_runtime(project_root)
    if runtime is None:
        print(json.dumps({"status": "failed", "message": "找不到可用Python运行环境。"}, ensure_ascii=False))
        return 1

    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root
        if not existing_pythonpath
        else os.pathsep.join([source_root, existing_pythonpath])
    )
    if BUNDLE_MANIFEST.is_file():
        environment["SHANGYU_SKILL_RUNTIME_MANIFEST_SHA256"] = hashlib.sha256(
            BUNDLE_MANIFEST.read_bytes()
        ).hexdigest()
    completed = subprocess.run(
        [
            runtime,
            "-m",
            "auto_shipped.cli",
            "verify-conversion-delivery",
            "--receipt",
            str(Path(args.receipt).expanduser().resolve()),
        ],
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
