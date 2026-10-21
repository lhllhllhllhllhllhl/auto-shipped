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
    REMOTE_STAGING_CONFIRMATION,
    ProtocolError,
    StageRequest,
)
from browser.guanyi.remote.server_runner import (
    initialize,
    operation_status,
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


if __name__ == "__main__":
    unittest.main()
