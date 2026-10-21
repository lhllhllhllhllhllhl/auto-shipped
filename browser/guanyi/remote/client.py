"""Client orchestration independent of the concrete remote transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .protocol import REMOTE_STAGING_CONFIRMATION, ProtocolError, StageRequest, validate_operation_id
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


__all__ = [
    "CLIENT_CONFIG_VERSION",
    "REMOTE_STAGING_CONFIRMATION",
    "RemoteGuanyiClient",
    "load_transport",
]
