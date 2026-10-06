#!/usr/bin/env python3
"""Install local dependencies for the bundled Shangyu runtime."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from runtime_common import (
    BUNDLED_PROJECT_ROOT,
    STATE_PROJECT_ROOT,
    STATE_ROOT,
    state_python_candidates,
    verify_bundled_runtime,
)


def candidate_executable(value: str) -> str | None:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path) if path.is_file() else None
    return shutil.which(value)


def compatible_python() -> str | None:
    seen: set[str] = set()
    for value in [*state_python_candidates(BUNDLED_PROJECT_ROOT), sys.executable]:
        command = candidate_executable(value)
        if not command or command in seen:
            continue
        seen.add(command)
        completed = subprocess.run(
            [command, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode == 0:
            return command
    return None


def main() -> int:
    integrity = verify_bundled_runtime()
    if not integrity.get("ready"):
        print(json.dumps({"status": "failed", "code": "BUNDLE_INTEGRITY_FAILED", "details": integrity}, ensure_ascii=False))
        return 1
    npm = shutil.which("npm")
    if npm is None:
        print(json.dumps({"status": "failed", "code": "NPM_MISSING"}, ensure_ascii=False))
        return 1
    base_python = compatible_python()
    if base_python is None:
        print(json.dumps({"status": "failed", "code": "PYTHON_311_MISSING"}, ensure_ascii=False))
        return 1

    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    if STATE_PROJECT_ROOT.exists():
        shutil.rmtree(STATE_PROJECT_ROOT)
    STATE_PROJECT_ROOT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLED_PROJECT_ROOT, STATE_PROJECT_ROOT)
    venv = STATE_ROOT / ".venv"
    venv_python = (
        venv / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else venv / "bin" / "python"
    )
    venv_compatible = False
    if venv_python.is_file():
        venv_compatible = subprocess.run(
            [str(venv_python), "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    if not venv_compatible:
        if venv.exists():
            shutil.rmtree(venv)
        subprocess.run([base_python, "-m", "venv", str(venv)], check=True)
    python = venv_python
    subprocess.run(
        [str(python), "-m", "pip", "install", "openpyxl>=3.1,<4"],
        check=True,
    )
    subprocess.run(
        [npm, "ci", "--omit=dev"],
        cwd=STATE_PROJECT_ROOT / "browser" / "guanyi",
        check=True,
    )
    print(json.dumps({"status": "configured", "runtime": str(STATE_PROJECT_ROOT), "stateRoot": str(STATE_ROOT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
