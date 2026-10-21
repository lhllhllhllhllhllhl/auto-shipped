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


def main() -> int:
    parser = argparse.ArgumentParser(description="调用私有管易远端预检执行端")
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
        else:
            result = client.status(args.operation_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ProtocolError as error:
        return fail(error.code, str(error), operation_id=stage_operation_id)


if __name__ == "__main__":
    raise SystemExit(main())
