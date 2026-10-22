"""OpenSSH transport; it streams files over stdin and never exposes a service port."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Mapping

from .protocol import ProtocolError, validate_operation_id


DEFAULT_RUNNER_COMMAND = "/opt/auto-shipped/current/deploy/guanyi-server/remote-job.sh"
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_ACTIONS = {
    "health",
    "init",
    "put-manifest",
    "put-artifact",
    "verify",
    "status",
    "prepare",
    "execute",
    "reconcile",
    "resolve-no-task",
    "prepare-retry",
    "probe",
    "login-start",
    "login-status",
    "login-credentials",
    "login-sms-request",
    "login-sms-submit",
}


@dataclass(frozen=True)
class SshTransportConfig:
    host: str
    user: str
    identity_file: Path | None = None
    port: int = 22
    known_hosts_file: Path | None = None
    runner_command: str = DEFAULT_RUNNER_COMMAND
    connect_timeout_seconds: int = 10
    command_timeout_seconds: int = 300

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SshTransportConfig":
        allowed = {
            "host",
            "user",
            "identity_file",
            "port",
            "known_hosts_file",
            "runner_command",
            "connect_timeout_seconds",
            "command_timeout_seconds",
        }
        if not set(value).issubset(allowed):
            raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 配置包含未登记字段。")
        host = str(value.get("host") or "")
        user = str(value.get("user") or "")
        if not _HOST.fullmatch(host) or host.startswith("-"):
            raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 主机格式无效。")
        if not _USER.fullmatch(user):
            raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 用户格式无效。")
        port = value.get("port", 22)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 端口无效。")
        runner = str(value.get("runner_command") or DEFAULT_RUNNER_COMMAND)
        runner_path = PurePosixPath(runner)
        if not runner_path.is_absolute() or any(part in {"", ".", ".."} for part in runner_path.parts):
            raise ProtocolError("REMOTE_CONFIG_INVALID", "服务器运行器路径无效。")
        identity = cls._optional_path(value.get("identity_file"), required=True)
        known_hosts = cls._optional_path(value.get("known_hosts_file"), required=False)
        connect_timeout = value.get("connect_timeout_seconds", 10)
        command_timeout = value.get("command_timeout_seconds", 300)
        if not isinstance(connect_timeout, int) or not 1 <= connect_timeout <= 60:
            raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 连接超时无效。")
        if not isinstance(command_timeout, int) or not 10 <= command_timeout <= 1800:
            raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 命令超时无效。")
        return cls(
            host=host,
            user=user,
            identity_file=identity,
            port=port,
            known_hosts_file=known_hosts,
            runner_command=runner,
            connect_timeout_seconds=connect_timeout,
            command_timeout_seconds=command_timeout,
        )

    @staticmethod
    def _optional_path(value: Any, *, required: bool) -> Path | None:
        if value in (None, ""):
            return None
        path = Path(str(value)).expanduser().resolve()
        if required and not path.is_file():
            raise ProtocolError("REMOTE_CONFIG_INVALID", "SSH 私钥文件不存在。")
        if not required and not path.is_file():
            raise ProtocolError("REMOTE_CONFIG_INVALID", "known_hosts 文件不存在。")
        return path


class SshTransport:
    def __init__(self, config: SshTransportConfig):
        self.config = config

    def build_command(self, action: str, operation_id: str) -> list[str]:
        if action not in _ACTIONS:
            raise ProtocolError("REMOTE_ACTION_INVALID", "未登记的服务器动作。")
        validate_operation_id(operation_id)
        options = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.config.connect_timeout_seconds}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-p",
            str(self.config.port),
        ]
        if self.config.identity_file is not None:
            options.extend(
                ["-o", "IdentitiesOnly=yes", "-i", str(self.config.identity_file)]
            )
        if self.config.known_hosts_file is not None:
            options.extend(
                ["-o", f"UserKnownHostsFile={self.config.known_hosts_file}"]
            )
        remote = shlex.join(
            [
                "sudo",
                "-n",
                self.config.runner_command,
                action,
                operation_id,
            ]
        )
        return [*options, f"{self.config.user}@{self.config.host}", remote]

    def invoke(
        self,
        action: str,
        operation_id: str,
        *,
        payload: bytes | None = None,
        stream: BinaryIO | None = None,
    ) -> dict:
        if payload is not None and stream is not None:
            raise ProtocolError("REMOTE_INPUT_INVALID", "一次远端调用只能提供一种输入。")
        command = self.build_command(action, operation_id)
        input_source: BinaryIO | int
        if stream is not None:
            input_source = stream
        else:
            input_source = subprocess.PIPE
        process = subprocess.Popen(
            command,
            stdin=input_source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "LC_ALL": "C"},
        )
        try:
            stdout, stderr = process.communicate(
                input=payload if stream is None else None,
                timeout=self.config.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise ProtocolError("REMOTE_COMMAND_TIMEOUT", "服务器调用超时，未执行浏览器写入。") from error
        if process.returncode != 0:
            try:
                failure = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                failure = None
            if isinstance(failure, dict) and failure.get("code"):
                raise ProtocolError(
                    str(failure["code"]),
                    str(failure.get("message") or "服务器拒绝了请求。"),
                )
            category = "ssh_failed" if stderr else "remote_runner_failed"
            raise ProtocolError(
                "REMOTE_TRANSPORT_FAILED",
                f"服务器连接或运行失败（{category}），未执行浏览器写入。",
            )
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError("REMOTE_RESPONSE_INVALID", "服务器返回了无法解析的结果。") from error
        if not isinstance(result, dict):
            raise ProtocolError("REMOTE_RESPONSE_INVALID", "服务器结果必须是 JSON 对象。")
        return result
