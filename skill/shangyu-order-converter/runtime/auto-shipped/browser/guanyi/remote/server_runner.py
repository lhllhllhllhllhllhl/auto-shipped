#!/usr/bin/env python3
"""Private SSH-invoked receiver for Guanyi batch preflight.

This runner deliberately has no login, navigation, file-selection, submission, or
reconciliation command. It only receives two explicitly described files and asks
the existing browser executor to verify their manifest binding.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from uuid import uuid4

from .protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    ProtocolError,
    StageRequest,
    digest_stream,
    validate_operation_id,
)


JOB_SCHEMA_VERSION = "guanyi-remote-job-state/1.0"
DEFAULT_JOB_ROOT = Path("/var/lib/auto-shipped/inbox")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def job_root() -> Path:
    configured = os.environ.get("GUANYI_REMOTE_JOB_ROOT")
    root = Path(configured).expanduser() if configured else DEFAULT_JOB_ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root.resolve()


def job_dir(operation_id: str) -> Path:
    return job_root() / validate_operation_id(operation_id)


@contextmanager
def job_lock(operation_id: str) -> Iterator[Path]:
    directory = job_dir(operation_id)
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    lock_path = directory / ".lock"
    with lock_path.open("a+b") as handle:
        lock_path.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield directory


def request_path(directory: Path) -> Path:
    return directory / "request.json"


def state_path(directory: Path) -> Path:
    return directory / "state.json"


def load_request(directory: Path) -> StageRequest:
    try:
        payload = request_path(directory).read_bytes()
    except OSError as error:
        raise ProtocolError("REMOTE_OPERATION_NOT_FOUND", "远端操作不存在或尚未初始化。") from error
    return StageRequest.from_json_bytes(payload)


def load_state(directory: Path) -> dict[str, Any]:
    try:
        value = json.loads(state_path(directory).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError("REMOTE_STATE_UNREADABLE", "远端操作状态不可读。") from error
    if not isinstance(value, dict) or value.get("schema_version") != JOB_SCHEMA_VERSION:
        raise ProtocolError("REMOTE_STATE_UNREADABLE", "远端操作状态版本无效。")
    return value


def public_state(request: StageRequest, state: dict[str, Any]) -> dict[str, Any]:
    response: dict[str, Any] = {
        "status": "remote_job_status",
        "operationId": request.operation_id,
        "state": state.get("state"),
        "received": dict(state.get("received") or {}),
        "artifact": {
            "fileName": request.artifact.file_name,
            "sizeBytes": request.artifact.size_bytes,
            "sha256": request.artifact.sha256,
        },
        "manifest": {
            "fileName": request.manifest.file_name,
            "sizeBytes": request.manifest.size_bytes,
            "sha256": request.manifest.sha256,
        },
        "safety": "No browser was opened and no upload was performed.",
    }
    verification = state.get("verification")
    if isinstance(verification, dict):
        response["verification"] = {
            key: verification.get(key)
            for key in ("status", "orders", "itemRows", "profileId")
            if verification.get(key) is not None
        }
    if state.get("code"):
        response["code"] = state["code"]
    return response


def initialize(operation_id: str, stream: BinaryIO) -> dict[str, Any]:
    payload = stream.read(64 * 1024 + 1)
    request = StageRequest.from_json_bytes(payload)
    if request.operation_id != validate_operation_id(operation_id):
        raise ProtocolError("REMOTE_OPERATION_ID_MISMATCH", "请求内外的操作 ID 不一致。")
    with job_lock(operation_id) as directory:
        existing_request = request_path(directory)
        if existing_request.exists():
            current = load_request(directory)
            if current != request:
                raise ProtocolError(
                    "REMOTE_OPERATION_CONFLICT",
                    "相同操作 ID 已绑定到另一组文件。",
                )
            return public_state(current, load_state(directory))
        existing_request.write_bytes(request.to_json_bytes())
        existing_request.chmod(0o600)
        state = {
            "schema_version": JOB_SCHEMA_VERSION,
            "operation_id": operation_id,
            "state": "receiving",
            "received": {"manifest": False, "artifact": False},
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        atomic_json(state_path(directory), state)
        return public_state(request, state)


def receive_file(operation_id: str, kind: str, stream: BinaryIO) -> dict[str, Any]:
    with job_lock(operation_id) as directory:
        request = load_request(directory)
        state = load_state(directory)
        descriptor = request.manifest if kind == "manifest" else request.artifact
        maximum = MAX_MANIFEST_BYTES if kind == "manifest" else MAX_ARTIFACT_BYTES
        destination = directory / descriptor.file_name
        if destination.exists():
            existing_size = destination.stat().st_size
            import hashlib

            existing_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
            if existing_size == descriptor.size_bytes and existing_hash == descriptor.sha256:
                state.setdefault("received", {})[kind] = True
                state["updated_at"] = now_iso()
                atomic_json(state_path(directory), state)
                return public_state(request, state)
            raise ProtocolError("REMOTE_FILE_CONFLICT", "远端操作已存在内容不同的同名文件。")
        temporary = directory / f".{kind}.{uuid4().hex}.part"
        try:
            with temporary.open("xb") as output:
                temporary.chmod(0o600)
                size, digest = digest_stream(stream, output, maximum=maximum)
                output.flush()
                os.fsync(output.fileno())
            if size != descriptor.size_bytes or digest != descriptor.sha256:
                raise ProtocolError("REMOTE_FILE_INTEGRITY_FAILED", "服务器接收文件后的大小或哈希不一致。")
            os.replace(temporary, destination)
            destination.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)
        state.setdefault("received", {})[kind] = True
        state["updated_at"] = now_iso()
        atomic_json(state_path(directory), state)
        return public_state(request, state)


def resolve_runtime_project() -> Path:
    explicit = os.environ.get("GUANYI_REMOTE_RUNTIME_PROJECT_ROOT")
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    skill_state = os.environ.get("SHANGYU_SKILL_STATE_ROOT")
    if skill_state:
        candidates.append(Path(skill_state).expanduser() / "runtime" / "auto-shipped")
    candidates.append(Path(__file__).resolve().parents[3])
    for candidate in candidates:
        browser = candidate / "browser" / "guanyi"
        if (browser / "cli.mjs").is_file() and (browser / "node_modules" / "playwright-core").is_dir():
            return candidate.resolve()
    raise ProtocolError("REMOTE_RUNTIME_NOT_READY", "服务器浏览器运行时尚未初始化。")


def verify_manifest(operation_id: str) -> dict[str, Any]:
    with job_lock(operation_id) as directory:
        request = load_request(directory)
        state = load_state(directory)
        if state.get("state") == "preflight_ready":
            result = public_state(request, state)
            result["status"] = "remote_preflight_ready"
            return result
        received = state.get("received") or {}
        if not received.get("manifest") or not received.get("artifact"):
            raise ProtocolError("REMOTE_FILES_INCOMPLETE", "服务器尚未完整收到 manifest 和 Excel。")
        project = resolve_runtime_project()
        browser_root = project / "browser" / "guanyi"
        manifest = directory / request.manifest.file_name
        artifact = directory / request.artifact.file_name
        completed = subprocess.run(
            [
                shutil.which("node") or "node",
                str(browser_root / "cli.mjs"),
                "verify-manifest",
                str(manifest),
                str(artifact),
            ],
            cwd=browser_root,
            env=os.environ.copy(),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        try:
            verification = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ProtocolError("REMOTE_VERIFIER_INVALID", "服务器 manifest 校验器返回异常。") from error
        if completed.returncode != 0 or verification.get("status") != "manifest_verified":
            state.update(
                {
                    "state": "preflight_rejected",
                    "code": str(verification.get("code") or "REMOTE_MANIFEST_REJECTED"),
                    "updated_at": now_iso(),
                }
            )
            atomic_json(state_path(directory), state)
            raise ProtocolError(
                state["code"],
                str(verification.get("message") or "服务器拒绝了 manifest 与 Excel 的绑定。"),
            )
        state.update(
            {
                "state": "preflight_ready",
                "verification": {
                    "status": "manifest_verified",
                    "orders": verification.get("orders"),
                    "itemRows": verification.get("itemRows"),
                    "profileId": verification.get("profileId"),
                },
                "updated_at": now_iso(),
            }
        )
        state.pop("code", None)
        atomic_json(state_path(directory), state)
        result = public_state(request, state)
        result["status"] = "remote_preflight_ready"
        return result


def operation_status(operation_id: str) -> dict[str, Any]:
    with job_lock(operation_id) as directory:
        return public_state(load_request(directory), load_state(directory))


def health() -> dict[str, Any]:
    root = job_root()
    runtime = resolve_runtime_project()
    return {
        "status": "ready",
        "service": "guanyi_remote_preflight",
        "schemaVersion": JOB_SCHEMA_VERSION,
        "runtimeReady": True,
        "jobRootReady": root.is_dir() and os.access(root, os.W_OK | os.X_OK),
        "supportedActions": ["stage", "status"],
        "safety": "No browser was opened and no upload was performed.",
        "runtime": runtime.name,
    }


def run(action: str, operation_id: str, stream: BinaryIO) -> dict[str, Any]:
    if action == "health":
        validate_operation_id(operation_id)
        return health()
    if action == "init":
        return initialize(operation_id, stream)
    if action == "put-manifest":
        return receive_file(operation_id, "manifest", stream)
    if action == "put-artifact":
        return receive_file(operation_id, "artifact", stream)
    if action == "verify":
        return verify_manifest(operation_id)
    if action == "status":
        return operation_status(operation_id)
    raise ProtocolError("REMOTE_ACTION_INVALID", "未登记的服务器动作。")


def main() -> int:
    parser = argparse.ArgumentParser(description="管易远端预检运行器")
    parser.add_argument(
        "action",
        choices=["health", "init", "put-manifest", "put-artifact", "verify", "status"],
    )
    parser.add_argument("operation_id")
    args = parser.parse_args()
    try:
        print_json(run(args.action, args.operation_id, sys.stdin.buffer))
        return 0
    except ProtocolError as error:
        print_json({"status": "failed", "code": error.code, "message": str(error)})
        return 2
    except subprocess.TimeoutExpired:
        print_json(
            {
                "status": "failed",
                "code": "REMOTE_VERIFIER_TIMEOUT",
                "message": "服务器 manifest 校验超时，未执行浏览器写入。",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
