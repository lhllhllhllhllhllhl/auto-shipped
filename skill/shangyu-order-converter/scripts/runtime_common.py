#!/usr/bin/env python3
"""Shared runtime discovery for the portable Shangyu skill package."""

from __future__ import annotations

import os
import hashlib
import json
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PROJECT_ROOT = SKILL_ROOT / "runtime" / "auto-shipped"
BUNDLE_MANIFEST = SKILL_ROOT / "runtime-manifest.json"
STATE_ROOT = Path(
    os.environ.get(
        "SHANGYU_SKILL_STATE_ROOT",
        str(Path.home() / ".codex" / "runtime" / "shangyu-order-converter"),
    )
).expanduser()
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
    return {
        "ready": not failures and expected_count == len(manifest.get("files", {})),
        "code": None if not failures else "BUNDLE_INTEGRITY_FAILED",
        "file_count": expected_count,
        "failures": failures[:20],
    }


def verify_bundled_runtime() -> dict:
    return verify_runtime_against_manifest(BUNDLED_PROJECT_ROOT)
