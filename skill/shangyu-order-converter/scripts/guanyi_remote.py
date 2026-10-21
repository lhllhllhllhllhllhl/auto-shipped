#!/usr/bin/env python3
"""Portable Skill entrypoint for the private Guanyi remote adapter."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from runtime_common import resolve_project_root, runtime_trust_status


def fail(code: str, message: str, *, operation_id: str | None = None) -> int:
    payload = {"status": "failed", "code": code, "message": message}
    if operation_id is not None:
        payload["operationId"] = operation_id
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1


def default_config_path() -> Path:
    configured = os.environ.get("GUANYI_REMOTE_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "auto-shipped" / "guanyi-remote.json"


def read_private_stdin(maximum: int) -> bytes:
    payload = sys.stdin.buffer.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("PRIVATE_INPUT_TOO_LARGE")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="调用私有管易远端执行端")
    parser.add_argument("--config", default=str(default_config_path()))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="只读检查远端预检执行端")
    stage = subparsers.add_parser("stage", help="传输并校验一个明确批次；不打开浏览器")
    stage.add_argument("manifest")
    stage.add_argument("excel")
    stage.add_argument("confirmation")
    stage.add_argument("--operation-id")
    status = subparsers.add_parser("status", help="读取一个远端暂存任务状态")
    status.add_argument("operation_id")
    prepare = subparsers.add_parser("prepare", help="建立远端浏览器操作；不打开浏览器")
    prepare.add_argument("operation_id")
    execute = subparsers.add_parser("execute", help="执行已明确授权的单批导入闭环")
    execute.add_argument("operation_id")
    execute.add_argument("authorization")
    reconcile = subparsers.add_parser("reconcile", help="只读核验可能已提交的远端任务")
    reconcile.add_argument("operation_id")
    subparsers.add_parser("probe", help="只读检查远端管易会话")
    subparsers.add_parser("login-start", help="启动远端临时凭据登录操作")
    login_status = subparsers.add_parser("login-status", help="读取远端登录操作状态")
    login_status.add_argument("login_operation_id")
    login_credentials = subparsers.add_parser(
        "login-credentials", help="从标准输入直传一次性账号密码 JSON"
    )
    login_credentials.add_argument("login_operation_id")
    login_credentials.add_argument("authorization")
    login_sms_request = subparsers.add_parser(
        "login-sms-request", help="在用户当次确认后请求登录短信"
    )
    login_sms_request.add_argument("login_operation_id")
    login_sms_request.add_argument("authorization")
    login_sms_submit = subparsers.add_parser(
        "login-sms-submit", help="从标准输入直传一次性短信验证码"
    )
    login_sms_submit.add_argument("login_operation_id")
    args = parser.parse_args()

    project_root = resolve_project_root()
    if project_root is None:
        return fail("RUNTIME_MISSING", "找不到 Skill 内置或外部订单运行时。")
    trust = runtime_trust_status(project_root, require_clean_development=True)
    if not trust.get("ready"):
        return fail(
            "REMOTE_RUNTIME_INTEGRITY_FAILED",
            "远端调用适配层与正式 Skill 清单不一致；请重新打包或提交当前开发版本。",
        )
    sys.path.insert(0, str(project_root))
    stage_operation_id: str | None = None
    try:
        from browser.guanyi.remote.client import RemoteGuanyiClient, load_transport
        from browser.guanyi.remote.protocol import ProtocolError

        client = RemoteGuanyiClient(load_transport(Path(args.config)))
        if args.command == "doctor":
            result = client.doctor()
        elif args.command == "stage":
            stage_operation_id = args.operation_id or str(uuid4())
            result = client.stage(
                manifest_path=Path(args.manifest),
                artifact_path=Path(args.excel),
                confirmation=args.confirmation,
                operation_id=stage_operation_id,
            )
        elif args.command == "status":
            result = client.status(args.operation_id)
        elif args.command == "prepare":
            result = client.prepare(args.operation_id)
        elif args.command == "execute":
            result = client.execute(args.operation_id, args.authorization)
        elif args.command == "reconcile":
            result = client.reconcile(args.operation_id)
        elif args.command == "probe":
            result = client.probe()
        elif args.command == "login-start":
            result = client.login_start()
        elif args.command == "login-status":
            result = client.login_status(args.login_operation_id)
        elif args.command == "login-credentials":
            result = client.login_credentials(
                args.login_operation_id,
                read_private_stdin(16 * 1024),
                args.authorization,
            )
        elif args.command == "login-sms-request":
            result = client.login_sms_request(
                args.login_operation_id, args.authorization
            )
        else:
            result = client.login_sms_submit(
                args.login_operation_id, read_private_stdin(128)
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("status") == "failed" else 0
    except ProtocolError as error:
        return fail(error.code, str(error), operation_id=stage_operation_id)
    except ValueError as error:
        return fail(str(error), "临时输入超过允许范围。")


if __name__ == "__main__":
    raise SystemExit(main())
