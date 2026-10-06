#!/usr/bin/env python3
"""Portable entrypoint for Feishu mapping status, routing, and proposals."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from convert_order import _find_runtime
from runtime_common import STATE_ROOT, resolve_project_root


COMMANDS = {
    "status": "feishu-mapping-status",
    "sync": "sync-feishu-official-mappings",
    "provision": "provision-pending-sheet",
    "submit": "submit-mapping-proposal",
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "code": "FEISHU_MAPPING_COMMAND_REQUIRED",
                    "message": "用法：feishu_mapping.py status|sync|provision|submit [参数]",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    project_root = resolve_project_root(None)
    if project_root is None:
        print('{"status":"invalid","code":"RUNTIME_MISSING"}')
        return 2
    runtime = _find_runtime(project_root)
    if runtime is None:
        print('{"status":"invalid","code":"PYTHON_RUNTIME_MISSING"}')
        return 2
    forwarded = list(sys.argv[2:])
    if sys.argv[1] == "sync":
        if "--catalog" not in forwarded:
            registry_path = project_root / "config/catalog/current_product_catalog.json"
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                catalog = Path(str(registry.get("path") or "")).expanduser()
                if not catalog.is_absolute():
                    catalog = project_root / catalog
            except (OSError, json.JSONDecodeError):
                catalog = Path()
            if not catalog.is_file():
                print('{"status":"invalid","code":"CATALOG_FILE_MISSING"}')
                return 2
            forwarded.extend(["--catalog", str(catalog.resolve())])
        if "--output" not in forwarded:
            snapshot = STATE_ROOT / "config" / "feishu-official-product-mappings.json"
            forwarded.extend(["--output", str(snapshot)])
    command = [
        runtime,
        "-m",
        "auto_shipped.cli",
        COMMANDS[sys.argv[1]],
        *forwarded,
    ]
    environment = os.environ.copy()
    source_root = str(project_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join([source_root, existing])
    )
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
