#!/usr/bin/env python3
"""Shared runtime discovery for the portable Shangyu skill package."""

from __future__ import annotations

import os
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PROJECT_ROOT = SKILL_ROOT / "runtime" / "auto-shipped"
BUNDLE_MANIFEST = SKILL_ROOT / "runtime-manifest.json"
def _path_can_hold_runtime(path: Path) -> bool:
    candidate = path.expanduser()
    existing = candidate
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK | os.X_OK):
        return False
    if candidate.exists() and not os.access(candidate, os.W_OK | os.X_OK):
        return False
    runtime = candidate / "runtime" / "auto-shipped"
    if runtime.exists() and not os.access(runtime, os.W_OK | os.X_OK):
        return False
    return True


def select_state_root() -> tuple[Path, str]:
    configured = os.environ.get("SHANGYU_SKILL_STATE_ROOT")
    if configured:
        return Path(configured).expanduser(), "configured"
    default = Path.home() / ".codex" / "runtime" / "shangyu-order-converter"
    if _path_can_hold_runtime(default):
        return default, "default"
    user_suffix = str(getattr(os, "getuid", lambda: "user")())
    fallback = Path(tempfile.gettempdir()) / f"shangyu-order-converter-{user_suffix}"
    return fallback, "temporary_fallback"


STATE_ROOT, STATE_ROOT_SOURCE = select_state_root()
STATE_PROJECT_ROOT = STATE_ROOT / "runtime" / "auto-shipped"


def is_project_root(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (
        path / "src" / "auto_shipped"
    ).is_dir()


def resolve_project_root(explicit: str | None = None) -> Path | None:
    configured = explicit or os.environ.get("SHANGYU_AUTO_SHIPPED_ROOT")
    state_runtime_ready = (
        is_project_root(STATE_PROJECT_ROOT)
        and verify_runtime_against_manifest(STATE_PROJECT_ROOT).get("ready") is True
    )
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.cwd(),
        STATE_PROJECT_ROOT if state_runtime_ready else None,
        BUNDLED_PROJECT_ROOT,
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.resolve()
        if is_project_root(resolved):
            return resolved
    return None


def state_python_candidates(project_root: Path) -> list[str]:
    configured = os.environ.get("SHANGYU_AUTO_SHIPPED_PYTHON")
    state_python = (
        STATE_ROOT / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else STATE_ROOT / ".venv" / "bin" / "python"
    )
    project_python = (
        project_root / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else project_root / ".venv" / "bin" / "python"
    )
    return [
        value
        for value in [
            configured,
            str(state_python),
            str(project_python),
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.11",
            "python3.13",
            "python3.12",
            "python3.11",
            "python3",
            "python",
            sys.executable,
        ]
        if value
    ]


def verify_runtime_against_manifest(project_root: Path) -> dict:
    try:
        manifest = json.loads(BUNDLE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ready": False, "code": "BUNDLE_MANIFEST_MISSING"}
    failures: list[str] = []
    for relative, expected in manifest.get("files", {}).items():
        path = project_root / relative
        if not path.is_file():
            failures.append(relative)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            failures.append(relative)
    expected_count = int(manifest.get("file_count") or 0)
    ready = not failures and expected_count == len(manifest.get("files", {}))
    return {
        "ready": ready,
        "code": None if ready else "BUNDLE_INTEGRITY_FAILED",
        "file_count": expected_count,
        "failures": failures[:20],
    }


def verify_bundled_runtime() -> dict:
    return verify_runtime_against_manifest(BUNDLED_PROJECT_ROOT)


def state_root_status(path: Path = STATE_ROOT) -> dict:
    ready = _path_can_hold_runtime(path)
    return {
        "ready": ready,
        "path": str(path),
        "source": STATE_ROOT_SOURCE if path == STATE_ROOT else "explicit_check",
        "persistent": STATE_ROOT_SOURCE != "temporary_fallback",
        "code": None if ready else "STATE_ROOT_NOT_WRITABLE",
    }


def runtime_trust_status(
    project_root: Path,
    *,
    require_clean_development: bool = False,
) -> dict:
    resolved = project_root.expanduser().resolve()
    if (resolved / ".git").exists():
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(resolved),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                    "--",
                    "browser/guanyi",
                    "skill/shangyu-order-converter/scripts/guanyi_browser.py",
                    "skill/shangyu-order-converter/scripts/guanyi_remote.py",
                    "skill/shangyu-order-converter/scripts/runtime_common.py",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return {
                "ready": False,
                "source": "external_development_checkout",
                "path": str(resolved),
                "development_clean": False,
                "code": "DEVELOPMENT_RUNTIME_GIT_UNAVAILABLE",
            }
        clean = completed.returncode == 0 and not completed.stdout.strip()
        return {
            "ready": clean if require_clean_development else completed.returncode == 0,
            "source": "external_development_checkout",
            "path": str(resolved),
            "development_clean": clean,
            "code": (
                "DEVELOPMENT_RUNTIME_DIRTY"
                if require_clean_development and not clean
                else None
            ),
        }
    integrity = verify_runtime_against_manifest(resolved)
    return {
        **integrity,
        "source": "verified_skill_runtime" if integrity.get("ready") else "unverified_runtime",
        "path": str(resolved),
    }
