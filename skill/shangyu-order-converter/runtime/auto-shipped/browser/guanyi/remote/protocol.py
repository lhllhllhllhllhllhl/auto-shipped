"""Versioned, path-free protocol for staging one Guanyi upload batch."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePath
from typing import Any, BinaryIO, Mapping


SCHEMA_VERSION = "guanyi-remote-job/1.0"
CONTROL_SCHEMA_VERSION = "guanyi-remote-control/1.0"
REMOTE_STAGING_CONFIRMATION = "CONFIRM_REMOTE_BATCH_STAGING"
REMOTE_SUBMIT_AUTHORIZATION = "CONFIRM_GUANYI_IMPORT_SUBMISSION"
REMOTE_SMS_REQUEST_AUTHORIZATION = "CONFIRM_REQUEST_GUANYI_SMS"
REMOTE_CREDENTIALS_AUTHORIZATION = "CONFIRM_TEMPORARY_CREDENTIALS_SUBMISSION"
REMOTE_NO_TASK_RESOLUTION_AUTHORIZATION = "CONFIRM_CLOSE_NO_TASK_OPERATION"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """A remote request violates the transport contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ControlRequest:
    schema_version: str
    action: str
    authorization: str

    @classmethod
    def build(cls, *, action: str, authorization: str) -> "ControlRequest":
        expected = {
            "execute": REMOTE_SUBMIT_AUTHORIZATION,
            "login-sms-request": REMOTE_SMS_REQUEST_AUTHORIZATION,
            "login-credentials": REMOTE_CREDENTIALS_AUTHORIZATION,
            "resolve-no-task": REMOTE_NO_TASK_RESOLUTION_AUTHORIZATION,
        }
        _require(action in expected, "REMOTE_ACTION_INVALID", "远端控制动作无效。")
        _require(
            authorization == expected[action],
            "REMOTE_ACTION_NOT_AUTHORIZED",
            "缺少当前动作的精确授权口令。",
        )
        return cls(
            schema_version=CONTROL_SCHEMA_VERSION,
            action=action,
            authorization=authorization,
        )

    @classmethod
    def from_json_bytes(cls, payload: bytes, *, expected_action: str) -> "ControlRequest":
        _require(len(payload) <= 4096, "REMOTE_CONTROL_TOO_LARGE", "远端控制请求过大。")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError("REMOTE_CONTROL_UNREADABLE", "远端控制请求不是有效 JSON。") from error
        _require(isinstance(value, Mapping), "REMOTE_CONTROL_INVALID", "远端控制请求必须是 JSON 对象。")
        _require(
            set(value) == {"schema_version", "action", "authorization"},
            "REMOTE_CONTROL_INVALID",
            "远端控制请求字段不完整或包含未登记字段。",
        )
        _require(
            value.get("schema_version") == CONTROL_SCHEMA_VERSION,
            "REMOTE_SCHEMA_UNSUPPORTED",
            "远端控制协议版本不受支持。",
        )
        _require(value.get("action") == expected_action, "REMOTE_ACTION_INVALID", "远端控制动作不匹配。")
        return cls.build(
            action=expected_action,
            authorization=str(value.get("authorization") or ""),
        )

    def to_json_bytes(self) -> bytes:
        return (
            json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ProtocolError(code, message)


def validate_operation_id(value: str) -> str:
    _require(
        isinstance(value, str) and bool(_OPERATION_ID.fullmatch(value)),
        "REMOTE_OPERATION_ID_INVALID",
        "远端操作 ID 只能包含字母、数字、点、下划线和连字符。",
    )
    return value


def validate_file_name(value: str, *, suffix: str) -> str:
    _require(isinstance(value, str) and bool(value), "REMOTE_FILE_NAME_INVALID", "文件名不能为空。")
    _require(
        value == PurePath(value).name and "/" not in value and "\\" not in value,
        "REMOTE_FILE_NAME_INVALID",
        "远端协议不接受路径，只接受文件名。",
    )
    _require(
        not value.startswith(".") and len(value.encode("utf-8")) <= 240,
        "REMOTE_FILE_NAME_INVALID",
        "文件名长度或格式无效。",
    )
    _require(
        value.lower().endswith(suffix),
        "REMOTE_FILE_TYPE_INVALID",
        f"文件必须使用 {suffix} 扩展名。",
    )
    return value


@dataclass(frozen=True)
class FileDescriptor:
    file_name: str
    size_bytes: int
    sha256: str

    @classmethod
    def from_path(cls, path: Path, *, suffix: str, maximum: int) -> "FileDescriptor":
        resolved = path.expanduser().resolve()
        _require(resolved.is_file(), "REMOTE_FILE_NOT_FOUND", "待传输文件不存在。")
        file_name = validate_file_name(resolved.name, suffix=suffix)
        size = resolved.stat().st_size
        _require(
            0 < size <= maximum,
            "REMOTE_FILE_SIZE_INVALID",
            "待传输文件大小超出允许范围。",
        )
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return cls(file_name=file_name, size_bytes=size, sha256=digest.hexdigest())

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        suffix: str,
        maximum: int,
    ) -> "FileDescriptor":
        _require(
            set(value) == {"file_name", "size_bytes", "sha256"},
            "REMOTE_DESCRIPTOR_INVALID",
            "远端文件描述字段不完整。",
        )
        file_name = validate_file_name(str(value.get("file_name") or ""), suffix=suffix)
        size = value.get("size_bytes")
        digest = value.get("sha256")
        _require(
            isinstance(size, int) and not isinstance(size, bool) and 0 < size <= maximum,
            "REMOTE_FILE_SIZE_INVALID",
            "远端文件大小无效。",
        )
        _require(
            isinstance(digest, str) and bool(_SHA256.fullmatch(digest)),
            "REMOTE_FILE_HASH_INVALID",
            "远端文件 SHA-256 无效。",
        )
        return cls(file_name=file_name, size_bytes=size, sha256=digest)


@dataclass(frozen=True)
class StageRequest:
    schema_version: str
    operation_id: str
    action: str
    manifest: FileDescriptor
    artifact: FileDescriptor
    safety: dict[str, bool | str]

    @classmethod
    def build(
        cls,
        *,
        operation_id: str,
        manifest_path: Path,
        artifact_path: Path,
        confirmation: str,
    ) -> "StageRequest":
        _require(
            confirmation == REMOTE_STAGING_CONFIRMATION,
            "REMOTE_STAGING_NOT_AUTHORIZED",
            "缺少当前批次的远端暂存授权。",
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            operation_id=validate_operation_id(operation_id),
            action="stage",
            manifest=FileDescriptor.from_path(
                manifest_path,
                suffix=".json",
                maximum=MAX_MANIFEST_BYTES,
            ),
            artifact=FileDescriptor.from_path(
                artifact_path,
                suffix=".xlsx",
                maximum=MAX_ARTIFACT_BYTES,
            ),
            safety={
                "remote_staging_authorized": True,
                "upload_authorized": False,
                "browser_write_allowed": False,
                "scope": "manifest_and_artifact_preflight_only",
            },
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StageRequest":
        _require(
            set(value)
            == {
                "schema_version",
                "operation_id",
                "action",
                "manifest",
                "artifact",
                "safety",
            },
            "REMOTE_REQUEST_INVALID",
            "远端请求字段不完整或包含未登记字段。",
        )
        _require(
            value.get("schema_version") == SCHEMA_VERSION,
            "REMOTE_SCHEMA_UNSUPPORTED",
            "远端任务协议版本不受支持。",
        )
        _require(value.get("action") == "stage", "REMOTE_ACTION_INVALID", "远端请求动作无效。")
        safety = value.get("safety")
        _require(isinstance(safety, Mapping), "REMOTE_SAFETY_INVALID", "远端安全边界缺失。")
        expected_safety = {
            "remote_staging_authorized": True,
            "upload_authorized": False,
            "browser_write_allowed": False,
            "scope": "manifest_and_artifact_preflight_only",
        }
        _require(dict(safety) == expected_safety, "REMOTE_SAFETY_INVALID", "远端安全边界无效。")
        manifest = value.get("manifest")
        artifact = value.get("artifact")
        _require(isinstance(manifest, Mapping), "REMOTE_DESCRIPTOR_INVALID", "manifest 描述无效。")
        _require(isinstance(artifact, Mapping), "REMOTE_DESCRIPTOR_INVALID", "Excel 描述无效。")
        return cls(
            schema_version=SCHEMA_VERSION,
            operation_id=validate_operation_id(str(value.get("operation_id") or "")),
            action="stage",
            manifest=FileDescriptor.from_mapping(
                manifest,
                suffix=".json",
                maximum=MAX_MANIFEST_BYTES,
            ),
            artifact=FileDescriptor.from_mapping(
                artifact,
                suffix=".xlsx",
                maximum=MAX_ARTIFACT_BYTES,
            ),
            safety=expected_safety,
        )

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> "StageRequest":
        _require(
            len(payload) <= 64 * 1024,
            "REMOTE_REQUEST_TOO_LARGE",
            "远端请求元数据过大。",
        )
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolError("REMOTE_REQUEST_UNREADABLE", "远端请求不是有效 JSON。") from error
        _require(isinstance(value, Mapping), "REMOTE_REQUEST_INVALID", "远端请求必须是 JSON 对象。")
        return cls.from_mapping(value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json_bytes(self) -> bytes:
        return (json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def digest_stream(stream: BinaryIO, destination: BinaryIO, *, maximum: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        _require(total <= maximum, "REMOTE_FILE_TOO_LARGE", "接收文件超出允许范围。")
        digest.update(chunk)
        destination.write(chunk)
    return total, digest.hexdigest()
