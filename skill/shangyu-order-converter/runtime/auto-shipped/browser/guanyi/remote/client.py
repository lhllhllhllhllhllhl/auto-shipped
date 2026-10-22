"""Client orchestration independent of the concrete remote transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .protocol import (
    REMOTE_CREDENTIALS_AUTHORIZATION,
    REMOTE_SMS_REQUEST_AUTHORIZATION,
    REMOTE_STAGING_CONFIRMATION,
    REMOTE_SUBMIT_AUTHORIZATION,
    REMOTE_NO_TASK_RESOLUTION_AUTHORIZATION,
    ControlRequest,
    ProtocolError,
    StageRequest,
    validate_operation_id,
)
from .ssh_transport import SshTransport, SshTransportConfig
from .transport import RemoteTransport, open_binary


CLIENT_CONFIG_VERSION = "guanyi-remote-client/1.0"


def load_transport(config_path: Path) -> RemoteTransport:
    path = config_path.expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError("REMOTE_CONFIG_UNREADABLE", "无法读取远端执行配置。") from error
    if not isinstance(payload, Mapping):
        raise ProtocolError("REMOTE_CONFIG_INVALID", "远端执行配置必须是 JSON 对象。")
    if set(payload) != {"schema_version", "transport", "ssh"}:
        raise ProtocolError("REMOTE_CONFIG_INVALID", "远端执行配置字段不完整或包含未登记字段。")
    if payload.get("schema_version") != CLIENT_CONFIG_VERSION:
        raise ProtocolError("REMOTE_CONFIG_UNSUPPORTED", "远端执行配置版本不受支持。")
    if payload.get("transport") != "ssh":
        raise ProtocolError("REMOTE_TRANSPORT_UNSUPPORTED", "当前版本只启用 SSH 传输插件。")
    ssh = payload.get("ssh")
    if not isinstance(ssh, Mapping):
        raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 传输配置缺失。")
    return SshTransport(SshTransportConfig.from_mapping(ssh))


class RemoteGuanyiClient:
    def __init__(self, transport: RemoteTransport):
        self.transport = transport

    def doctor(self) -> dict[str, Any]:
        return self.transport.invoke("health", "healthcheck")

    def stage(
        self,
        *,
        manifest_path: Path,
        artifact_path: Path,
        confirmation: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        request = StageRequest.build(
            operation_id=operation_id or str(uuid4()),
            manifest_path=manifest_path,
            artifact_path=artifact_path,
            confirmation=confirmation,
        )
        self.transport.invoke(
            "init",
            request.operation_id,
            payload=request.to_json_bytes(),
        )
        with open_binary(manifest_path) as manifest:
            self.transport.invoke("put-manifest", request.operation_id, stream=manifest)
        with open_binary(artifact_path) as artifact:
            self.transport.invoke("put-artifact", request.operation_id, stream=artifact)
        return self.transport.invoke("verify", request.operation_id)

    def status(self, operation_id: str) -> dict[str, Any]:
        return self.transport.invoke("status", validate_operation_id(operation_id))

    def prepare(self, operation_id: str) -> dict[str, Any]:
        return self.transport.invoke("prepare", validate_operation_id(operation_id))

    def execute(self, operation_id: str, authorization: str) -> dict[str, Any]:
        control = ControlRequest.build(action="execute", authorization=authorization)
        return self.transport.invoke(
            "execute",
            validate_operation_id(operation_id),
            payload=control.to_json_bytes(),
        )

    def reconcile(self, operation_id: str) -> dict[str, Any]:
        return self.transport.invoke("reconcile", validate_operation_id(operation_id))

    def resolve_no_task(
        self, operation_id: str, authorization: str
    ) -> dict[str, Any]:
        control = ControlRequest.build(
            action="resolve-no-task", authorization=authorization
        )
        return self.transport.invoke(
            "resolve-no-task",
            validate_operation_id(operation_id),
            payload=control.to_json_bytes(),
        )

    def prepare_retry(self, operation_id: str) -> dict[str, Any]:
        return self.transport.invoke(
            "prepare-retry", validate_operation_id(operation_id)
        )

    def probe(self) -> dict[str, Any]:
        return self.transport.invoke("probe", "probe")

    def login_start(self) -> dict[str, Any]:
        return self.transport.invoke("login-start", "login-start")

    def login_status(self, login_operation_id: str) -> dict[str, Any]:
        return self.transport.invoke(
            "login-status", validate_operation_id(login_operation_id)
        )

    def login_credentials(
        self,
        login_operation_id: str,
        credentials: bytes,
        authorization: str,
    ) -> dict[str, Any]:
        control = ControlRequest.build(
            action="login-credentials", authorization=authorization
        )
        return self.transport.invoke(
            "login-credentials",
            validate_operation_id(login_operation_id),
            payload=control.to_json_bytes() + credentials,
        )

    def login_sms_request(
        self, login_operation_id: str, authorization: str
    ) -> dict[str, Any]:
        control = ControlRequest.build(
            action="login-sms-request", authorization=authorization
        )
        return self.transport.invoke(
            "login-sms-request",
            validate_operation_id(login_operation_id),
            payload=control.to_json_bytes(),
        )

    def login_sms_submit(
        self, login_operation_id: str, code: bytes
    ) -> dict[str, Any]:
        return self.transport.invoke(
            "login-sms-submit",
            validate_operation_id(login_operation_id),
            payload=code,
        )


__all__ = [
    "CLIENT_CONFIG_VERSION",
    "REMOTE_CREDENTIALS_AUTHORIZATION",
    "REMOTE_SMS_REQUEST_AUTHORIZATION",
    "REMOTE_NO_TASK_RESOLUTION_AUTHORIZATION",
    "REMOTE_STAGING_CONFIRMATION",
    "REMOTE_SUBMIT_AUTHORIZATION",
    "RemoteGuanyiClient",
    "load_transport",
]
