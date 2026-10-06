#!/usr/bin/env python3
"""Read-only environment check for the portable Shangyu skill."""

from __future__ import annotations

import json
import platform
import os
import shutil
import subprocess
from pathlib import Path

from runtime_common import (
    BUNDLED_PROJECT_ROOT,
    STATE_PROJECT_ROOT,
    resolve_project_root,
    state_python_candidates,
    verify_runtime_against_manifest,
)


def executable(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return str(candidate) if candidate.is_file() else None
    return shutil.which(value)


def python_status(project_root: Path) -> dict:
    for value in state_python_candidates(project_root):
        command = executable(value)
        if not command:
            continue
        completed = subprocess.run(
            [command, "-c", "import sys, openpyxl; print(sys.version.split()[0], openpyxl.__version__) if sys.version_info >= (3, 11) else sys.exit(1)"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            version, openpyxl_version = completed.stdout.strip().split(maxsplit=1)
            return {"ready": True, "executable": command, "version": version, "openpyxl": openpyxl_version}
    return {"ready": False, "code": "PYTHON_RUNTIME_MISSING"}


def catalog_status(project_root: Path) -> dict:
    registry_path = project_root / "config" / "catalog" / "current_product_catalog.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ready": False, "code": "CATALOG_REGISTRY_MISSING"}
    value = registry.get("path")
    if not value:
        return {"ready": False, "code": "CATALOG_PATH_MISSING"}
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return {
        "ready": path.is_file(),
        "code": None if path.is_file() else "CATALOG_FILE_MISSING",
        "path": str(path.resolve()),
    }


def main() -> int:
    project_root = resolve_project_root()
    if project_root is None:
        payload = {"status": "needs_setup", "checks": {"runtime": {"ready": False, "code": "RUNTIME_MISSING"}}}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    npm = shutil.which("npm")
    lark_cli = shutil.which("lark-cli")
    browser_root = project_root / "browser" / "guanyi"
    if platform.system() == "Darwin":
        chrome_candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    elif platform.system() == "Windows":
        chrome_candidates = [
            Path(value) / "Google" / "Chrome" / "Application" / "chrome.exe"
            for value in [
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            ]
            if value
        ]
    else:
        chrome_candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
        ]
    chrome = next((path for path in chrome_candidates if path.is_file()), None)
    checks = {
        "runtime": {"ready": True, "path": str(project_root)},
        "bundle_integrity": (
            verify_runtime_against_manifest(project_root)
            if project_root in {
                BUNDLED_PROJECT_ROOT.resolve(),
                STATE_PROJECT_ROOT.resolve(),
            }
            else {"ready": True, "source": "external_development_runtime"}
        ),
        "python": python_status(project_root),
        "node": {"ready": shutil.which("node") is not None},
        "npm": {"ready": npm is not None},
        "lark_cli": {
            "ready": lark_cli is not None,
            "path": lark_cli,
            "required_for": "fallback_feishu_mapping_gateway",
            "optional_when_agent_has_native_feishu_sheets": True,
        },
        "playwright_core": {"ready": (browser_root / "node_modules" / "playwright-core").is_dir()},
        "chrome": {"ready": chrome is not None, "path": str(chrome) if chrome else None},
        "template": {"ready": (project_root / "assets" / "templates" / "guanyi" / "自定义订单导入模板.xlsx").is_file()},
        "catalog": catalog_status(project_root),
        "credential_provider": {
            "ready": platform.system() in {"Darwin", "Windows"},
            "provider": (
                "macos_keychain"
                if platform.system() == "Darwin"
                else "windows_dpapi"
                if platform.system() == "Windows"
                else None
            ),
        },
    }
    required_checks = {
        key: value for key, value in checks.items() if key != "lark_cli"
    }
    ready = all(value.get("ready") for value in required_checks.values())
    print(json.dumps({"status": "ready" if ready else "needs_setup", "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
