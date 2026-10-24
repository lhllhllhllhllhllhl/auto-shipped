from __future__ import annotations

import hashlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser.guanyi.remote.client import RemoteGuanyiClient
from browser.guanyi.remote.protocol import (
    REMOTE_CREDENTIALS_AUTHORIZATION,
    REMOTE_NO_TASK_RESOLUTION_AUTHORIZATION,
    REMOTE_SMS_REQUEST_AUTHORIZATION,
    REMOTE_STAGING_CONFIRMATION,
    REMOTE_SUBMIT_AUTHORIZATION,
    ControlRequest,
    ProtocolError,
    StageRequest,
)
from browser.guanyi.remote.server_runner import (
    BrowserCommandResult,
    atomic_json,
    execute_operation,
    initialize,
    load_state,
    login_credentials,
    operation_status,
    prepare_operation,
    prepare_retry_operation,
    receive_file,
    reconcile_operation,
    resolve_no_task_operation,
    state_path,
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
        client.resolve_no_task(
            "remote-client-002", REMOTE_NO_TASK_RESOLUTION_AUTHORIZATION
        )
        client.prepare_retry("remote-client-002")
        self.assertEqual(
            [call[0] for call in transport.calls],
            [
                "execute",
                "login-credentials",
                "login-sms-request",
                "login-sms-submit",
                "resolve-no-task",
                "prepare-retry",
            ],
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
        source.mkdir(exist_ok=True)
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
                        second["retryPolicy"]["queueDisposition"],
                        "block_upload_queue",
                    )
                self.assertEqual(
                    operation_status("remote-server-004")["state"],
                    "needs_manual_review",
                )
                with self.assertRaises(ProtocolError) as raised:
                    execute_operation(
                        "remote-server-004", io.BytesIO(control.to_json_bytes())
                    )
                self.assertEqual(raised.exception.code, "REMOTE_RESUBMISSION_BLOCKED")

    def test_quarantined_submit_allows_other_batches_but_stays_reconcilable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            control = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            browser_operation_id = "00000000-0000-4000-8000-000000000014"
            prepared = BrowserCommandResult(
                [{"status": "operation_prepared", "operationId": browser_operation_id}],
                0,
            )
            reconciling = BrowserCommandResult(
                [{"status": "reconciling", "state": "reconciling"}],
                0,
            )
            quarantined = BrowserCommandResult(
                [
                    {
                        "status": "quarantined",
                        "state": "quarantined",
                        "reason": "submitted_task_not_visible_after_quarantine_window",
                        "quarantineAfterMs": 300000,
                    }
                ],
                0,
            )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-014")
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[prepared, reconciling, quarantined],
                ):
                    prepare_operation("remote-server-014")
                    execute_operation(
                        "remote-server-014", io.BytesIO(control.to_json_bytes())
                    )
                    result = reconcile_operation("remote-server-014")
                self.assertEqual(result["status"], "quarantined")
                self.assertEqual(
                    result["retryPolicy"]["queueDisposition"],
                    "continue_other_batches",
                )
                status = operation_status("remote-server-014")
                self.assertEqual(status["state"], "quarantined")
                self.assertTrue(status["received"]["manifest"])
                self.assertIn("Never resubmit this batch", status["safety"])

    def test_verified_task_binding_releases_later_batches_before_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            control = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            browser_operation_id = "00000000-0000-4000-8000-000000000015"
            prepared = BrowserCommandResult(
                [{"status": "operation_prepared", "operationId": browser_operation_id}],
                0,
            )
            task_observed = BrowserCommandResult(
                [
                    {
                        "status": "reconciling",
                        "state": "task_id_observed",
                        "reason": "task_still_running",
                        "task": {
                            "taskId": "707700015",
                            "taskName": "自定义订单导入",
                            "status": "执行中",
                        },
                    }
                ],
                2,
            )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-015")
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[prepared, task_observed],
                ):
                    prepare_operation("remote-server-015")
                    result = execute_operation(
                        "remote-server-015", io.BytesIO(control.to_json_bytes())
                    )
                self.assertEqual(result["state"], "task_id_observed")
                self.assertEqual(
                    result["retryPolicy"]["queueDisposition"],
                    "continue_other_batches",
                )
                status = operation_status("remote-server-015")
                self.assertEqual(status["state"], "task_observed")
                self.assertIn("verified Guanyi task ID", status["safety"])

    def test_unbound_submitted_job_blocks_a_later_remote_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            control = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-016a")
                self._preflight_job(root, "remote-server-016b")
                prepared_a = BrowserCommandResult(
                    [{"status": "operation_prepared", "operationId": "00000000-0000-4000-8000-000000000016"}],
                    0,
                )
                reconciling_a = BrowserCommandResult(
                    [{"status": "reconciling", "state": "reconciling"}],
                    2,
                )
                prepared_b = BrowserCommandResult(
                    [{"status": "operation_prepared", "operationId": "00000000-0000-4000-8000-000000000017"}],
                    0,
                )
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[prepared_a, reconciling_a, prepared_b],
                ):
                    prepare_operation("remote-server-016a")
                    execute_operation(
                        "remote-server-016a", io.BytesIO(control.to_json_bytes())
                    )
                    prepare_operation("remote-server-016b")
                    with self.assertRaises(ProtocolError) as raised:
                        execute_operation(
                            "remote-server-016b", io.BytesIO(control.to_json_bytes())
                        )
                self.assertEqual(raised.exception.code, "REMOTE_UPLOAD_QUEUE_BLOCKED")

    def test_task_bound_manual_review_does_not_block_a_later_remote_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            control = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-017a")
                self._preflight_job(root, "remote-server-017b")
                first = root / "jobs" / "remote-server-017a"
                reviewed = load_state(first)
                reviewed.update(
                    {
                        "state": "needs_manual_review",
                        "last_result": {
                            "status": "needs_manual_review",
                            "task": {"taskId": "707700017"},
                        },
                    }
                )
                atomic_json(state_path(first), reviewed)
                prepared_b = BrowserCommandResult(
                    [{"status": "operation_prepared", "operationId": "00000000-0000-4000-8000-000000000018"}],
                    0,
                )
                task_observed_b = BrowserCommandResult(
                    [
                        {
                            "status": "reconciling",
                            "state": "task_id_observed",
                            "task": {"taskId": "707700018"},
                        }
                    ],
                    2,
                )
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[prepared_b, task_observed_b],
                ):
                    prepare_operation("remote-server-017b")
                    result = execute_operation(
                        "remote-server-017b", io.BytesIO(control.to_json_bytes())
                    )
                self.assertEqual(result["state"], "task_id_observed")

    def test_visible_ambiguous_tasks_release_later_distinct_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            control = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-018a")
                self._preflight_job(root, "remote-server-018b")
                prepared_a = BrowserCommandResult(
                    [{"status": "operation_prepared", "operationId": "00000000-0000-4000-8000-000000000019"}],
                    0,
                )
                ambiguous_a = BrowserCommandResult(
                    [
                        {
                            "status": "needs_manual_review",
                            "reason": "multiple_new_tasks",
                            "candidates": [
                                {"taskId": "707700019"},
                                {"taskId": "707700020"},
                            ],
                        }
                    ],
                    2,
                )
                prepared_b = BrowserCommandResult(
                    [{"status": "operation_prepared", "operationId": "00000000-0000-4000-8000-000000000021"}],
                    0,
                )
                task_observed_b = BrowserCommandResult(
                    [
                        {
                            "status": "reconciling",
                            "state": "task_id_observed",
                            "task": {"taskId": "707700021"},
                        }
                    ],
                    2,
                )
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[
                        prepared_a,
                        ambiguous_a,
                        prepared_b,
                        task_observed_b,
                    ],
                ):
                    prepare_operation("remote-server-018a")
                    ambiguous = execute_operation(
                        "remote-server-018a", io.BytesIO(control.to_json_bytes())
                    )
                    self.assertEqual(
                        ambiguous["retryPolicy"]["queueDisposition"],
                        "continue_other_batches",
                    )
                    first_status = operation_status("remote-server-018a")
                    self.assertEqual(
                        first_status["state"], "tasks_observed_ambiguous"
                    )
                    self.assertIn("must never be resubmitted", first_status["safety"])

                    prepare_operation("remote-server-018b")
                    result = execute_operation(
                        "remote-server-018b", io.BytesIO(control.to_json_bytes())
                    )
                self.assertEqual(result["state"], "task_id_observed")

    def test_upload_rule_gate_rejection_remains_prewrite_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            control = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-005")
                prepared = BrowserCommandResult(
                    [
                        {
                            "status": "operation_prepared",
                            "operationId": "00000000-0000-4000-8000-000000000005",
                        }
                    ],
                    0,
                )
                gate_blocked = BrowserCommandResult(
                    [
                        {
                            "status": "login_operation_blocked",
                            "code": "UPLOAD_RULE_GATE_BLOCKED",
                        }
                    ],
                    1,
                )
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=[prepared, gate_blocked, gate_blocked],
                ):
                    prepare_operation("remote-server-005")
                    first = execute_operation(
                        "remote-server-005", io.BytesIO(control.to_json_bytes())
                    )
                    self.assertEqual(first["code"], "UPLOAD_RULE_GATE_BLOCKED")
                    self.assertEqual(
                        operation_status("remote-server-005")["state"], "prepared"
                    )
                    second = execute_operation(
                        "remote-server-005", io.BytesIO(control.to_json_bytes())
                    )
                    self.assertEqual(second["code"], "UPLOAD_RULE_GATE_BLOCKED")

    def test_safe_no_task_resolution_prepares_two_retries_then_defers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = {"GUANYI_REMOTE_JOB_ROOT": str(root / "jobs")}
            submit = ControlRequest.build(
                action="execute", authorization=REMOTE_SUBMIT_AUTHORIZATION
            )
            resolve = ControlRequest.build(
                action="resolve-no-task",
                authorization=REMOTE_NO_TASK_RESOLUTION_AUTHORIZATION,
            )
            operation_ids = [
                "00000000-0000-4000-8000-000000000010",
                "00000000-0000-4000-8000-000000000011",
                "00000000-0000-4000-8000-000000000012",
            ]
            results = [
                BrowserCommandResult(
                    [{"status": "operation_prepared", "operationId": operation_ids[0], "attemptNumber": 1}],
                    0,
                )
            ]
            for index in range(3):
                results.extend(
                    [
                        BrowserCommandResult(
                            [{"status": "submission_blocked", "code": "FIELD_MAPPING_VERIFICATION_FAILED"}],
                            2,
                        ),
                        BrowserCommandResult(
                            [{"status": "operation_retryable_no_task", "state": "retryable_failed", "attemptNumber": index + 1}],
                            0,
                        ),
                    ]
                )
                if index < 2:
                    results.append(
                        BrowserCommandResult(
                            [{
                                "status": "operation_prepared",
                                "operationId": operation_ids[index + 1],
                                "attemptNumber": index + 2,
                                "retryOf": operation_ids[index],
                            }],
                            0,
                        )
                    )
            with patch.dict(os.environ, environment, clear=False):
                self._preflight_job(root, "remote-server-006")
                with patch(
                    "browser.guanyi.remote.server_runner.run_browser_command",
                    side_effect=results,
                ):
                    prepare_operation("remote-server-006")
                    for retry_index in range(3):
                        execute_operation(
                            "remote-server-006", io.BytesIO(submit.to_json_bytes())
                        )
                        closed = resolve_no_task_operation(
                            "remote-server-006", io.BytesIO(resolve.to_json_bytes())
                        )
                        if retry_index < 2:
                            self.assertEqual(
                                closed["retryPolicy"]["queueDisposition"],
                                "retry_same_batch",
                            )
                            prepared = prepare_retry_operation("remote-server-006")
                            self.assertEqual(
                                prepared["retryPolicy"]["safeRetryCount"],
                                retry_index + 1,
                            )
                        else:
                            self.assertEqual(
                                closed["retryPolicy"]["queueDisposition"],
                                "defer_to_tail",
                            )
                    final = operation_status("remote-server-006")
                    self.assertEqual(final["state"], "deferred")
                    self.assertEqual(final["retryPolicy"]["safeRetryCount"], 2)


class GuanyiBrowserSkillGateTests(unittest.TestCase):
    def test_resident_start_audits_and_propagates_upload_rule_readiness(self) -> None:
        scripts = PROJECT_ROOT / "skill/shangyu-order-converter/scripts"
        original_path = list(sys.path)
        try:
            sys.path.insert(0, str(scripts))
            module = runpy.run_path(str(scripts / "guanyi_browser.py"))
        finally:
            sys.path[:] = original_path
        self.assertIn("resident-start", module["UPLOAD_RULE_AUDIT_COMMANDS"])
        self.assertIn("resident-start", module["UPLOAD_RULE_READY_COMMANDS"])


if __name__ == "__main__":
    unittest.main()
