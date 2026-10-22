from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser.guanyi.remote.client import RemoteGuanyiClient
from browser.guanyi.remote.protocol import (
    REMOTE_CREDENTIALS_AUTHORIZATION,
    REMOTE_SMS_REQUEST_AUTHORIZATION,
    REMOTE_STAGING_CONFIRMATION,
    REMOTE_SUBMIT_AUTHORIZATION,
    ControlRequest,
    ProtocolError,
    StageRequest,
)
from browser.guanyi.remote.server_runner import (
    BrowserCommandResult,
    execute_operation,
    initialize,
    login_credentials,
    operation_status,
    prepare_operation,
    receive_file,
    verify_manifest,
)
from browser.guanyi.remote.ssh_transport import SshTransport, SshTransportConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_fixture(root: Path, *, artifact_bytes: bytes = b"synthetic-xlsx") -> tuple[Path, Path]:
    artifact = root / "orders.xlsx"
    artifact.write_bytes(artifact_bytes)
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "upload-batch/1.0",
                "status": "ready",
                "platform": "guanyi",
                "operation": "custom_order_import",
                "profile": {"profile_id": "guanyi_order_import_v1", "version": 1},
                "artifact": {
                    "path": "/source/environment/orders.xlsx",
                    "file_name": "orders.xlsx",
                    "sha256": digest,
                    "size_bytes": len(artifact_bytes),
                },
                "counts": {"orders": 1, "item_rows": 1},
                "audit_scope": {"platform_order_numbers": ["REMOTE-TEST-001"]},
                "checks": {"fixture": True},
                "issues": [],
                "safety": {
                    "manifest_contains_recipient_pii": False,
                    "workbook_contains_recipient_pii": True,
                    "upload_authorized": False,
                    "audit_authorized": False,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest, artifact


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes | None]] = []

    def invoke(self, action, operation_id, *, payload=None, stream=None):
        body = stream.read() if stream is not None else payload
        self.calls.append((action, operation_id, body))
        return {
            "status": "remote_preflight_ready" if action == "verify" else "ok",
            "operationId": operation_id,
            "state": "preflight_ready" if action == "verify" else "receiving",
        }


class GuanyiRemoteProtocolTests(unittest.TestCase):
    def test_request_contains_no_local_paths_or_upload_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, artifact = write_fixture(root)
            request = StageRequest.build(
                operation_id="remote-test-001",
                manifest_path=manifest,
                artifact_path=artifact,
                confirmation=REMOTE_STAGING_CONFIRMATION,
            )
            encoded = request.to_json_bytes().decode("utf-8")
            self.assertNotIn(str(root), encoded)
            self.assertFalse(request.safety["upload_authorized"])
            self.assertFalse(request.safety["browser_write_allowed"])

    def test_request_rejects_missing_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, artifact = write_fixture(Path(temporary))
            with self.assertRaisesRegex(ProtocolError, "暂存授权"):
                StageRequest.build(
                    operation_id="remote-test-002",
                    manifest_path=manifest,
                    artifact_path=artifact,
                    confirmation="",
                )

    def test_protocol_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, artifact = write_fixture(Path(temporary))
            request = StageRequest.build(
                operation_id="remote-test-003",
                manifest_path=manifest,
                artifact_path=artifact,
                confirmation=REMOTE_STAGING_CONFIRMATION,
            ).to_dict()
            request["artifact"]["file_name"] = "../orders.xlsx"
            with self.assertRaises(ProtocolError) as raised:
                StageRequest.from_mapping(request)
            self.assertEqual(raised.exception.code, "REMOTE_FILE_NAME_INVALID")

    def test_control_request_requires_exact_action_authorization(self) -> None:
        request = ControlRequest.build(
            action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
        )
        restored = ControlRequest.from_json_bytes(
            request.to_json_bytes(), expected_action="execute"
        )
        self.assertEqual(restored.authorization, REMOTE_SUBMIT_AUTHORIZATION)
        with self.assertRaises(ProtocolError) as raised:
            ControlRequest.build(action="execute", authorization="yes")
        self.assertEqual(raised.exception.code, "REMOTE_ACTION_NOT_AUTHORIZED")


class GuanyiRemoteClientTests(unittest.TestCase):
    def test_stage_uses_only_receive_and_verify_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, artifact = write_fixture(Path(temporary))
            transport = FakeTransport()
            result = RemoteGuanyiClient(transport).stage(
                manifest_path=manifest,
                artifact_path=artifact,
                confirmation=REMOTE_STAGING_CONFIRMATION,
                operation_id="remote-client-001",
            )
            self.assertEqual(result["status"], "remote_preflight_ready")
            self.assertEqual(
                [call[0] for call in transport.calls],
                ["init", "put-manifest", "put-artifact", "verify"],
            )
            self.assertNotIn("execute", [call[0] for call in transport.calls])

    def test_write_and_login_controls_use_stdin_payloads(self) -> None:
        transport = FakeTransport()
        client = RemoteGuanyiClient(transport)
        client.execute("remote-client-002", REMOTE_SUBMIT_AUTHORIZATION)
        client.login_credentials(
            "00000000-0000-4000-8000-000000000001",
            b'{"username":"fixture-user","password":"fixture-password"}\n',
            REMOTE_CREDENTIALS_AUTHORIZATION,
        )
        client.login_sms_request(
            "00000000-0000-4000-8000-000000000001",
            REMOTE_SMS_REQUEST_AUTHORIZATION,
        )
        client.login_sms_submit(
            "00000000-0000-4000-8000-000000000001", b"123456\n"
        )
        self.assertEqual(
            [call[0] for call in transport.calls],
            ["execute", "login-credentials", "login-sms-request", "login-sms-submit"],
        )
        self.assertNotIn("fixture-password", " ".join(transport.calls[1][:2]))
        self.assertIn(b"fixture-password", transport.calls[1][2])


class SshTransportTests(unittest.TestCase):
    def test_command_uses_host_key_checking_and_fixed_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "key"
            key.write_text("fixture", encoding="utf-8")
            config = SshTransportConfig.from_mapping(
                {
                    "host": "example.internal",
                    "user": "ubuntu",
                    "identity_file": str(key),
                }
            )
            command = SshTransport(config).build_command("status", "op-001")
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn("IdentitiesOnly=yes", command)
            self.assertIn("ubuntu@example.internal", command)
            self.assertIn("remote-job.sh status op-001", command[-1])

    def test_command_rejects_shell_metacharacters_in_operation_id(self) -> None:
        config = SshTransportConfig(host="example.internal", user="ubuntu")
        with self.assertRaises(ProtocolError):
            SshTransport(config).build_command("status", "op;uname")


class GuanyiRemoteServerRunnerTests(unittest.TestCase):
    def _preflight_job(self, root: Path, operation_id: str) -> tuple[Path, Path]:
        source = root / "source"
        source.mkdir()
        manifest, artifact = write_fixture(source)
        request = StageRequest.build(
            operation_id=operation_id,
            manifest_path=manifest,
            artifact_path=artifact,
            confirmation=REMOTE_STAGING_CONFIRMATION,
        )
        initialize(operation_id, io.BytesIO(request.to_json_bytes()))
        with manifest.open("rb") as handle:
            receive_file(operation_id, "manifest", handle)
        with artifact.open("rb") as handle:
            receive_file(operation_id, "artifact", handle)
        state_file = root / "jobs" / operation_id / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["state"] = "preflight_ready"
        state_file.write_text(json.dumps(state), encoding="utf-8")
        return manifest, artifact

    def test_server_reuses_existing_manifest_verifier_and_is_idempotent(self) -> None:
        browser_dependencies = PROJECT_ROOT / "browser/guanyi/node_modules/playwright-core"
        if not browser_dependencies.is_dir():
            self.skipTest("browser dependencies are not installed")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            manifest, artifact = write_fixture(source)
            request = StageRequest.build(
                operation_id="remote-server-001",
                manifest_path=manifest,
                artifact_path=artifact,
                confirmation=REMOTE_STAGING_CONFIRMATION,
            )
            environment = {
                "GUANYI_REMOTE_JOB_ROOT": str(root / "jobs"),
                "GUANYI_REMOTE_RUNTIME_PROJECT_ROOT": str(PROJECT_ROOT),
            }
            with patch.dict(os.environ, environment, clear=False):
                initialized = initialize(request.operation_id, io.BytesIO(request.to_json_bytes()))
                self.assertEqual(initialized["state"], "receiving")
                with manifest.open("rb") as handle:
                    receive_file(request.operation_id, "manifest", handle)
                with artifact.open("rb") as handle:
                    receive_file(request.operation_id, "artifact", handle)
                verified = verify_manifest(request.operation_id)
                self.assertEqual(verified["status"], "remote_preflight_ready")
                self.assertEqual(verified["state"], "preflight_ready")
                self.assertEqual(verified["verification"]["orders"], 1)
                again = verify_manifest(request.operation_id)
                self.assertEqual(again["state"], "preflight_ready")
                status = operation_status(request.operation_id)
                self.assertEqual(status["received"], {"manifest": True, "artifact": True})

    def test_same_operation_rejects_different_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            manifest_one, artifact_one = write_fixture(first, artifact_bytes=b"first")
            manifest_two, artifact_two = write_fixture(second, artifact_bytes=b"second")
            request_one = StageRequest.build(
                operation_id="remote-server-002",
                manifest_path=manifest_one,
                artifact_path=artifact_one,
                confirmation=REMOTE_STAGING_CONFIRMATION,
            )
            request_two = StageRequest.build(
                operation_id="remote-server-002",
                manifest_path=manifest_two,
                artifact_path=artifact_two,
                confirmation=REMOTE_STAGING_CONFIRMATION,
            )
            with patch.dict(os.environ, {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}, clear=False):
                initialize(request_one.operation_id, io.BytesIO(request_one.to_json_bytes()))
                with self.assertRaises(ProtocolError) as raised:
                    initialize(request_two.operation_id, io.BytesIO(request_two.to_json_bytes()))
                self.assertEqual(raised.exception.code, "REMOTE_OPERATION_CONFLICT")

    def test_prepare_and_execute_delegate_to_existing_browser_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-003")
                prepared_result = BrowserCommandResult(
                    [
                        {
                            "status": "operation_prepared",
                            "operationId": "00000000-0000-4000-8000-000000000002",
                            "orders": 1,
                            "itemRows": 1,
                        }
                    ],
                    0,
                )
                verified_result = BrowserCommandResult(
                    [
                        {"status": "session_ready"},
                        {
                            "status": "verified",
                            "state": "verified",
                            "operationId": "00000000-0000-4000-8000-000000000002",
                            "task": {"taskId": "task-1", "taskStatus": "已完成"},
                            "importResult": {
                                "summary": {"total": 1, "succeeded": 1, "failed": 0},
                                "failures": [],
                                "visibleFailureCount": 0,
                                "detailsTruncated": False,
                            },
                        },
                    ],
                    0,
                )
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[prepared_result, verified_result],
                ) as runner:
                    prepared = prepare_operation("remote-server-003")
                    control = ControlRequest.build(
                        action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
                    )
                    executed = execute_operation(
                        "remote-server-003", io.BytesIO(control.to_json_bytes())
                    )
                self.assertEqual(prepared["state"], "prepared")
                self.assertEqual(executed["status"], "verified")
                self.assertEqual(executed["remoteOperationId"], "remote-server-003")
                self.assertEqual(runner.call_args_list[0].args[0], "prepare-operation")
                self.assertEqual(
                    runner.call_args_list[1].args[0], "resident-execute-import"
                )
                status = operation_status("remote-server-003")
                self.assertEqual(status["state"], "verified")
                with self.assertRaises(ProtocolError) as raised:
                    execute_operation(
                        "remote-server-003", io.BytesIO(control.to_json_bytes())
                    )
                self.assertEqual(raised.exception.code, "REMOTE_RESUBMISSION_BLOCKED")

    def test_temporary_credentials_are_forwarded_without_job_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            login_id = "00000000-0000-4000-8000-000000000003"
            control = ControlRequest.build(
                action="login-credentials",
                authorization=REMOTE_CREDENTIALS_AUTHORIZATION,
            )
            secret = b'{"username":"fixture-user","password":"fixture-password"}\n'
            with patch.dict(os.environ, environment, clear=False), patch(
                "browser.guanyi.remote.server_runner.run_browser_command",
                return_value=BrowserCommandResult(
                    [{"status": "authenticating", "loginOperationId": login_id}], 0
                ),
            ) as runner:
                result = login_credentials(
                    login_id, io.BytesIO(control.to_json_bytes() + secret)
                )
            self.assertEqual(result["status"], "authenticating")
            self.assertEqual(runner.call_args.kwargs["stdin_payload"], secret)
            persisted = b"".join(
                path.read_bytes() for path in root.rglob("*") if path.is_file()
            )
            self.assertNotIn(b"fixture-user", persisted)
            self.assertNotIn(b"fixture-password", persisted)

    def test_prewrite_login_result_remains_retryable_but_ambiguous_write_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            control = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-004")
                prepared = BrowserCommandResult(
                    [
                        {
                            "status": "operation_prepared",
                            "operationId": "00000000-0000-4000-8000-000000000004",
                        }
                    ],
                    0,
                )
                needs_login = BrowserCommandResult(
                    [
                        {"status": "authenticating"},
                        {"status": "needs_verification", "state": "needs_verification"},
                    ],
                    2,
                )
                ambiguous = BrowserCommandResult(
                    [
                        {"status": "session_ready"},
                        {"status": "form_prepared"},
                        {"status": "submission_blocked", "code": "UNKNOWN_WRITE_OUTCOME"},
                    ],
                    3,
                )
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[prepared, needs_login, ambiguous],
                ):
                    prepare_operation("remote-server-004")
                    first = execute_operation(
                        "remote-server-004", io.BytesIO(control.to_json_bytes())
                    )
                    self.assertEqual(first["status"], "needs_verification")
                    self.assertEqual(operation_status("remote-server-004")["state"], "prepared")
                    second = execute_operation(
                        "remote-server-004", io.BytesIO(control.to_json_bytes())
                    )
                    self.assertEqual(second["status"], "submission_blocked")
                self.assertEqual(
                    operation_status("remote-server-004")["state"],
                    "needs_manual_review",
                )
                with self.assertRaises(ProtocolError) as raised:
                    execute_operation(
                        "remote-server-004", io.BytesIO(control.to_json_bytes())
                    )
                self.assertEqual(raised.exception.code, "REMOTE_RESUBMISSION_BLOCKED")


if __name__ == "__main__":
    unittest.main()
