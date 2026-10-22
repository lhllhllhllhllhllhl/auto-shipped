#!/usr/bin/env python3
"""Private SSH-invoked adapter for the existing Guanyi browser executor.

The adapter owns transport, staging, authorization gates, and remote job state.
Every browser action is delegated to the single ``browser/guanyi`` state machine;
credentials and SMS codes are streamed to that executor and are never persisted.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from uuid import uuid4

from .protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    REMOTE_CREDENTIALS_AUTHORIZATION,
    REMOTE_SMS_REQUEST_AUTHORIZATION,
    REMOTE_SUBMIT_AUTHORIZATION,
    ControlRequest,
    ProtocolError,
    StageRequest,
    digest_stream,
    validate_operation_id,
)


JOB_SCHEMA_VERSION = "guanyi-remote-job-state/1.0"
DEFAULT_JOB_ROOT = Path("/var/lib/auto-shipped/inbox")
EXECUTOR_TIMEOUT_SECONDS = 300
LOGIN_TIMEOUT_SECONDS = 180
_SMS_CODE = re.compile(rb"^[0-9]{4,6}$")


class BrowserCommandResult:
    def __init__(self, documents: list[dict[str, Any]], returncode: int):
        self.documents = documents
        self.returncode = returncode

    @property
    def final(self) -> dict[str, Any]:
        return self.documents[-1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        "safety": (
            "A write may have been attempted; do not resubmit. Reconcile this exact operation."
            if state.get("execute_started")
            else "No browser write was requested for this remote job."
        ),
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
    if state.get("browser_operation_id"):
        response["browserOperationId"] = state["browser_operation_id"]
    if isinstance(state.get("last_result"), dict):
        response["lastResult"] = state["last_result"]
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


def _parse_json_documents(output: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    documents: list[dict[str, Any]] = []
    offset = 0
    while offset < len(output):
        while offset < len(output) and output[offset].isspace():
            offset += 1
        if offset >= len(output):
            break
        try:
            value, end = decoder.raw_decode(output, offset)
        except json.JSONDecodeError as error:
            raise ProtocolError(
                "REMOTE_EXECUTOR_RESPONSE_INVALID",
                "服务器浏览器执行器返回了无法解析的结果。",
            ) from error
        if not isinstance(value, dict):
            raise ProtocolError(
                "REMOTE_EXECUTOR_RESPONSE_INVALID",
                "服务器浏览器执行器结果必须是 JSON 对象。",
            )
        documents.append(value)
        offset = end
    if not documents:
        raise ProtocolError(
            "REMOTE_EXECUTOR_RESPONSE_INVALID",
            "服务器浏览器执行器没有返回机器可读结果。",
        )
    return documents


def run_browser_command(
    command: str,
    *arguments: str,
    stdin_payload: bytes | None = None,
    timeout: int = EXECUTOR_TIMEOUT_SECONDS,
) -> BrowserCommandResult:
    project = resolve_runtime_project()
    entrypoint = (
        Path(__file__).resolve().parents[3]
        / "skill"
        / "shangyu-order-converter"
        / "scripts"
        / "guanyi_browser.py"
    )
    if not entrypoint.is_file():
        raise ProtocolError("REMOTE_RUNTIME_NOT_READY", "服务器缺少正式浏览器 Skill 入口。")
    environment = os.environ.copy()
    environment["GUANYI_MACHINE_OUTPUT"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(entrypoint),
            "--project-root",
            str(project),
            command,
            *arguments,
        ],
        cwd=project,
        env=environment,
        input=stdin_payload,
        check=False,
        capture_output=True,
        timeout=timeout,
    )
    try:
        stdout = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(
            "REMOTE_EXECUTOR_RESPONSE_INVALID",
            "服务器浏览器执行器返回了非 UTF-8 结果。",
        ) from error
    return BrowserCommandResult(_parse_json_documents(stdout), completed.returncode)


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "status",
        "state",
        "reason",
        "code",
        "message",
        "task",
        "importResult",
        "verificationType",
        "requiresUserAction",
        "userAction",
        "instruction",
        "loginOperationId",
        "createdAt",
        "updatedAt",
        "expiresAt",
        "browserMode",
        "queuePolicy",
        "safety",
    }
    return {key: result[key] for key in allowed if key in result}


def _remote_result(
    remote_operation_id: str,
    browser_operation_id: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(result)
    payload["remoteOperationId"] = remote_operation_id
    if browser_operation_id:
        payload["browserOperationId"] = browser_operation_id
    return payload


def prepare_operation(operation_id: str) -> dict[str, Any]:
    with job_lock(operation_id) as directory:
        request = load_request(directory)
        state = load_state(directory)
        if state.get("browser_operation_id"):
            return {
                "status": "remote_operation_prepared",
                "remoteOperationId": operation_id,
                "browserOperationId": state["browser_operation_id"],
                "state": state.get("state"),
                "safety": "No browser was opened and no upload was performed.",
            }
        if state.get("state") != "preflight_ready":
            raise ProtocolError(
                "REMOTE_PREFLIGHT_REQUIRED",
                "远端任务必须先完成 manifest 与 Excel 预检。",
            )
        manifest = directory / request.manifest.file_name
        artifact = directory / request.artifact.file_name
        command = run_browser_command(
            "prepare-operation", str(manifest), str(artifact), timeout=60
        )
        result = command.final
        if command.returncode != 0 or result.get("status") != "operation_prepared":
            state.update(
                {
                    "state": "prepare_rejected",
                    "code": str(result.get("code") or "REMOTE_PREPARE_REJECTED"),
                    "last_result": _safe_result(result),
                    "updated_at": now_iso(),
                }
            )
            atomic_json(state_path(directory), state)
            raise ProtocolError(
                state["code"], str(result.get("message") or "服务器未能准备上传操作。")
            )
        browser_operation_id = str(result.get("operationId") or "")
        validate_operation_id(browser_operation_id)
        state.update(
            {
                "state": "prepared",
                "browser_operation_id": browser_operation_id,
                "execute_started": False,
                "last_result": _safe_result(result),
                "updated_at": now_iso(),
            }
        )
        state.pop("code", None)
        atomic_json(state_path(directory), state)
        return {
            "status": "remote_operation_prepared",
            "remoteOperationId": operation_id,
            "browserOperationId": browser_operation_id,
            "state": "prepared",
            "orders": result.get("orders"),
            "itemRows": result.get("itemRows"),
            "safety": "No browser was opened and no upload was performed.",
        }


def execute_operation(operation_id: str, stream: BinaryIO) -> dict[str, Any]:
    control = ControlRequest.from_json_bytes(stream.read(4097), expected_action="execute")
    if control.authorization != REMOTE_SUBMIT_AUTHORIZATION:
        raise ProtocolError("REMOTE_ACTION_NOT_AUTHORIZED", "缺少当前批次的提交授权。")
    with job_lock(operation_id) as directory:
        load_request(directory)
        state = load_state(directory)
        browser_operation_id = state.get("browser_operation_id")
        if not browser_operation_id:
            raise ProtocolError("REMOTE_PREPARE_REQUIRED", "远端任务尚未准备浏览器操作。")
        if state.get("execute_started"):
            raise ProtocolError(
                "REMOTE_RESUBMISSION_BLOCKED",
                "该任务可能已发出写请求；禁止重复提交，只能执行结果核验。",
            )
        if state.get("state") != "prepared":
            raise ProtocolError("REMOTE_OPERATION_NOT_READY", "远端任务当前不能提交。")
        state.update(
            {
                "state": "submit_maybe_sent",
                "execute_started": True,
                "updated_at": now_iso(),
            }
        )
        atomic_json(state_path(directory), state)
        try:
            command = run_browser_command(
                "resident-execute-import",
                browser_operation_id,
                REMOTE_SUBMIT_AUTHORIZATION,
                timeout=15 * 60,
            )
        except (ProtocolError, subprocess.TimeoutExpired):
            state.update(
                {
                    "state": "needs_manual_review",
                    "code": "REMOTE_EXECUTION_OUTCOME_UNKNOWN",
                    "updated_at": now_iso(),
                }
            )
            atomic_json(state_path(directory), state)
            raise
        result = command.final
        progress = {str(item.get("status") or "") for item in command.documents[:-1]}
        prewrite_statuses = {
            "needs_login",
            "needs_verification",
            "credential_unavailable",
            "browser_session_busy",
            "platform_browser_unavailable",
            "local_verification_required",
            "login_blocked",
            "login_failed",
            "waiting_for_credentials",
            "resident_browser_blocked",
        }
        status = str(result.get("status") or result.get("state") or "")
        safely_prewrite = status in prewrite_statuses or (
            status == "login_operation_blocked"
            and result.get("code") == "RESIDENT_SESSION_LOGIN_EXPIRED"
        )
        if safely_prewrite and progress.issubset({"authenticating"}):
            state.update(
                {
                    "state": "prepared",
                    "execute_started": False,
                    "last_result": _safe_result(result),
                    "updated_at": now_iso(),
                }
            )
        elif status == "verified":
            state.update(
                {
                    "state": "verified",
                    "last_result": _safe_result(result),
                    "updated_at": now_iso(),
                }
            )
        elif status == "reconciling":
            state.update(
                {
                    "state": "reconciling",
                    "last_result": _safe_result(result),
                    "updated_at": now_iso(),
                }
            )
        else:
            state.update(
                {
                    "state": "needs_manual_review",
                    "last_result": _safe_result(result),
                    "updated_at": now_iso(),
                }
            )
        atomic_json(state_path(directory), state)
        return _remote_result(operation_id, browser_operation_id, result)


def reconcile_operation(operation_id: str) -> dict[str, Any]:
    with job_lock(operation_id) as directory:
        load_request(directory)
        state = load_state(directory)
        browser_operation_id = state.get("browser_operation_id")
        if not browser_operation_id or not state.get("execute_started"):
            raise ProtocolError(
                "REMOTE_RECONCILIATION_NOT_READY",
                "该任务尚未进入可能已提交状态，无需结果核验。",
            )
        command = run_browser_command(
            "resident-reconcile", browser_operation_id, timeout=15 * 60
        )
        result = command.final
        status = str(result.get("status") or result.get("state") or "")
        if status == "verified":
            remote_state = "verified"
        elif status == "reconciling":
            remote_state = "reconciling"
        else:
            remote_state = "needs_manual_review"
        state.update(
            {
                "state": remote_state,
                "last_result": _safe_result(result),
                "updated_at": now_iso(),
            }
        )
        atomic_json(state_path(directory), state)
        return _remote_result(operation_id, browser_operation_id, result)


def _read_control_line(stream: BinaryIO, expected_action: str) -> tuple[ControlRequest, bytes]:
    payload = stream.read(20 * 1024 + 1)
    if len(payload) > 20 * 1024:
        raise ProtocolError("REMOTE_INPUT_TOO_LARGE", "远端临时输入过大。")
    line, separator, remainder = payload.partition(b"\n")
    if not separator:
        raise ProtocolError("REMOTE_CONTROL_UNREADABLE", "远端控制请求缺少输入正文。")
    control = ControlRequest.from_json_bytes(line, expected_action=expected_action)
    return control, remainder


def login_credentials(operation_id: str, stream: BinaryIO) -> dict[str, Any]:
    control, credentials = _read_control_line(stream, "login-credentials")
    if control.authorization != REMOTE_CREDENTIALS_AUTHORIZATION:
        raise ProtocolError("REMOTE_ACTION_NOT_AUTHORIZED", "缺少临时凭据提交授权。")
    try:
        value = json.loads(credentials.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("REMOTE_CREDENTIALS_INVALID", "临时凭据输入格式无效。") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"username", "password"}
        or not isinstance(value.get("username"), str)
        or not value["username"]
        or not isinstance(value.get("password"), str)
        or not value["password"]
    ):
        raise ProtocolError("REMOTE_CREDENTIALS_INVALID", "临时凭据输入格式无效。")
    forwarded = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    result = run_browser_command(
        "login-credentials-submit",
        operation_id,
        REMOTE_CREDENTIALS_AUTHORIZATION,
        stdin_payload=forwarded,
        timeout=LOGIN_TIMEOUT_SECONDS,
    ).final
    value["username"] = ""
    value["password"] = ""
    return result


def login_sms_submit(operation_id: str, stream: BinaryIO) -> dict[str, Any]:
    code = stream.read(129).strip()
    if not _SMS_CODE.fullmatch(code):
        raise ProtocolError("REMOTE_SMS_CODE_INVALID", "短信验证码必须是 4 至 6 位数字。")
    return run_browser_command(
        "login-sms-submit",
        operation_id,
        stdin_payload=code + b"\n",
        timeout=LOGIN_TIMEOUT_SECONDS,
    ).final


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
        "service": "guanyi_remote_executor",
        "schemaVersion": JOB_SCHEMA_VERSION,
        "runtimeReady": True,
        "jobRootReady": root.is_dir() and os.access(root, os.W_OK | os.X_OK),
        "supportedActions": [
            "stage",
            "status",
            "prepare",
            "execute",
            "reconcile",
            "probe",
            "login",
        ],
        "safety": "Health checks do not open a browser or perform a write.",
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
    if action == "prepare":
        return prepare_operation(operation_id)
    if action == "execute":
        return execute_operation(operation_id, stream)
    if action == "reconcile":
        return reconcile_operation(operation_id)
    if action == "probe":
        return run_browser_command("resident-probe").final
    if action == "login-start":
        return run_browser_command("resident-start", timeout=60).final
    if action == "login-status":
        return run_browser_command("login-status", operation_id, timeout=60).final
    if action == "login-credentials":
        return login_credentials(operation_id, stream)
    if action == "login-sms-request":
        control = ControlRequest.from_json_bytes(
            stream.read(4097), expected_action="login-sms-request"
        )
        if control.authorization != REMOTE_SMS_REQUEST_AUTHORIZATION:
            raise ProtocolError("REMOTE_ACTION_NOT_AUTHORIZED", "缺少本次短信发送授权。")
        return run_browser_command(
            "login-sms-request",
            operation_id,
            REMOTE_SMS_REQUEST_AUTHORIZATION,
            timeout=60,
        ).final
    if action == "login-sms-submit":
        return login_sms_submit(operation_id, stream)
    raise ProtocolError("REMOTE_ACTION_INVALID", "未登记的服务器动作。")


def main() -> int:
    parser = argparse.ArgumentParser(description="管易远端执行适配器")
    parser.add_argument(
        "action",
        choices=[
            "health",
            "init",
            "put-manifest",
            "put-artifact",
            "verify",
            "status",
            "prepare",
            "execute",
            "reconcile",
            "probe",
            "login-start",
            "login-status",
            "login-credentials",
            "login-sms-request",
            "login-sms-submit",
        ],
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
                "code": "REMOTE_EXECUTOR_TIMEOUT",
                "message": "服务器执行器超时；若已进入提交阶段，禁止重试并应查询当前任务状态。",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
