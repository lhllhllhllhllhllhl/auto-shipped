#!/usr/bin/env python3
"""Portable Skill entrypoint for the Guanyi browser executor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from runtime_common import STATE_ROOT, resolve_project_root


COMMANDS = {
    "login": "login",
    "login-auto": "login-auto",
    "login-auto-start": "login-auto-start",
    "login-agent-start": "login-agent-start",
    "login-credentials-ready": "login-credentials-ready",
    "login-status": "login-status",
    "login-sms-request": "login-sms-request",
    "login-sms-submit": "login-sms-submit",
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
    "submit-import": "submit-import",
    "execute-import": "execute-import",
    "reconcile-operation": "reconcile-operation",
    "resolve-no-task": "resolve-no-task",
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

    if args.command in {"submit-import", "execute-import"}:
        audit = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "audit_rules.py"),
                "--phase",
                "upload",
                "--project-root",
                str(project_root),
            ],
            cwd=browser_root,
            env=os.environ.copy(),
            check=False,
            capture_output=True,
            text=True,
        )
        if audit.returncode != 0:
            return fail(
                "UPLOAD_RULE_GATE_BLOCKED",
                "上传规则门禁尚未全部通过；Skill 不执行管易确定提交。请检查规则状态、实现引用和运行时打包是否一致。",
            )

    environment = os.environ.copy()
    if args.command in {"submit-import", "execute-import"}:
        environment["GUANYI_UPLOAD_RULES_READY"] = "1"
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
