#!/usr/bin/env python3
"""Portable entrypoint for Feishu mapping status, routing, and proposals."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from convert_order import _find_runtime, _resolve_preferred_catalogs
from runtime_common import STATE_ROOT, resolve_project_root


COMMANDS = {
    "status": "feishu-mapping-status",
    "diagnose-recovery": "diagnose-official-repository-recovery",
    "recover": "recover-official-repository",
    "sync": "sync-feishu-official-mappings",
    "sync-native": "sync-feishu-official-mappings-from-payload",
    "provision": "provision-pending-sheet",
    "submit": "submit-mapping-proposal",
    "build-feedback": "build-correction-feedback",
    "submit-feedback": "submit-correction-feedback",
    "collect-feedback": "collect-correction-feedback",
    "plan-publish": "plan-pending-mapping-publish",
    "plan-canonical": "plan-canonical-mapping-migration",
    "migrate-canonical": "migrate-canonical-mapping-schema",
    "publish": "publish-pending-mappings",
    "add": "direct-add-official-mapping",
    "add-store-route": "direct-add-manual-store-route",
    "add-expansion": "direct-add-item-expansion",
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "code": "FEISHU_MAPPING_COMMAND_REQUIRED",
                    "message": "用法：feishu_mapping.py status|sync|sync-native|diagnose-recovery|recover|provision|submit|build-feedback|submit-feedback|collect-feedback|plan-publish|plan-canonical|migrate-canonical|publish|add|add-store-route|add-expansion [参数]",
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
    if sys.argv[1] in {
        "sync",
        "sync-native",
        "diagnose-recovery",
        "recover",
        "plan-publish",
        "plan-canonical",
        "migrate-canonical",
        "publish",
        "add",
        "add-store-route",
        "add-expansion",
    }:
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
        if "--preferred-catalog" not in forwarded:
            try:
                preferred_catalogs = _resolve_preferred_catalogs(project_root)
            except FileNotFoundError as exc:
                print(
                    json.dumps(
                        {
                            "status": "invalid",
                            "code": "PREFERRED_CATALOG_FILE_MISSING",
                            "message": str(exc),
                        },
                        ensure_ascii=False,
                    )
                )
                return 2
            for preferred_catalog in preferred_catalogs:
                forwarded.extend(
                    ["--preferred-catalog", str(preferred_catalog)]
                )
        if sys.argv[1] in {"sync", "sync-native"} and "--output" not in forwarded:
            snapshot = STATE_ROOT / "config" / "feishu-official-product-mappings.json"
            forwarded.extend(["--output", str(snapshot)])
        if sys.argv[1] in {
            "migrate-canonical",
            "add",
            "add-store-route",
            "add-expansion",
        } and "--output" not in forwarded:
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
