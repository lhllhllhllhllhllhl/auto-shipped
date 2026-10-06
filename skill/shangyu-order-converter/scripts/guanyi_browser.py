#!/usr/bin/env python3
"""Portable Skill entrypoint for the Guanyi browser executor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from runtime_common import STATE_ROOT, resolve_project_root


COMMANDS = {
    "login": "login",
    "login-auto": "login-auto",
    "login-auto-sms": "login-auto-sms",
    "login-auto-sms-task-center": "login-auto-sms-task-center",
    "credential-set": "credential:set",
    "credential-web": "credential:web",
    "credential-status": "credential:status",
    "probe": "probe",
    "navigate-import": "navigate-import",
    "prepare-import-form": "prepare-import-form",
    "probe-task-center": "probe-task-center",
    "probe-completed-imports": "probe-completed-imports",
    "capture-task-baseline": "capture-task-baseline",
    "inspect-file-selection": "inspect-file-selection",
    "select-import-file": "select-import-file",
    "verify-manifest": "verify-manifest",
    "prepare-operation": "prepare-operation",
}


def fail(code: str, message: str) -> int:
    print(json.dumps({"status": "failed", "code": code, "message": message}, ensure_ascii=False, indent=2))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="运行管易浏览器执行器")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--project-root")
    args = parser.parse_args()

    project_root = resolve_project_root(args.project_root)
    if project_root is None:
        return fail("RUNTIME_MISSING", "找不到 Skill 内置或外部订单转换运行时。")
    browser_root = project_root / "browser" / "guanyi"
    if not (browser_root / "package.json").is_file():
        return fail("BROWSER_RUNTIME_MISSING", "运行时缺少管易浏览器模块。")
    npm = shutil.which("npm")
    if npm is None:
        return fail("NPM_MISSING", "找不到 npm，请先运行 Skill 环境初始化。")
    if not (browser_root / "node_modules" / "playwright-core").is_dir():
        return fail(
            "NODE_DEPENDENCIES_MISSING",
            "管易浏览器依赖尚未安装，请先运行 bootstrap_runtime.py。",
        )

    environment = os.environ.copy()
    environment.setdefault(
        "GUANYI_RUNTIME_ROOT",
        str(STATE_ROOT / "guanyi-browser"),
    )
    command = [npm, "run", COMMANDS[args.command]]
    if args.arguments:
        command.extend(["--", *args.arguments])
    completed = subprocess.run(
        command,
        cwd=browser_root,
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
