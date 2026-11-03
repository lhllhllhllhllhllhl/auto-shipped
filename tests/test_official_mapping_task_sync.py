from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "skill" / "shangyu-order-converter" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import convert_orders  # noqa: E402


def _args(
    *,
    snapshot: Path | None = None,
    receipt: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        official_mapping_snapshot=str(snapshot) if snapshot else None,
        official_mapping_receipt=str(receipt) if receipt else None,
        stage_timeout_seconds=120.0,
        mappings=None,
    )


class OfficialMappingTaskSyncTests(unittest.TestCase):
    def test_existing_old_snapshot_is_not_reused_when_sync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "config" / "feishu-official-product-mappings.json"
            old.parent.mkdir(parents=True)
            old.write_text('{"mapping_revision":"old"}', encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout=json.dumps(
                    {
                        "status": "failed",
                        "code": "FEISHU_OFFICIAL_MAPPING_SYNC_FAILED",
                        "message": "飞书当前不可用",
                    }
                ),
                stderr="",
            )
            with (
                patch.object(convert_orders, "STATE_ROOT", root),
                patch.object(convert_orders.subprocess, "run", return_value=completed),
            ):
                snapshot, receipt, error = convert_orders._prepare_official_mapping(
                    _args(),
                    sys.executable,
                    root,
                    root / "catalog.csv",
                    (),
                )

        self.assertIsNone(snapshot)
        self.assertIsNone(receipt)
        self.assertIn("飞书当前不可用", error)

    def test_every_invocation_runs_sync_into_a_distinct_task_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def complete_sync(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output = Path(command[command.index("--output") + 1])
                receipt = Path(command[command.index("--receipt-output") + 1])
                output.write_text('{"source":"fresh"}', encoding="utf-8")
                receipt.write_text('{"status":"synced"}', encoding="utf-8")
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout="{}",
                    stderr="",
                )

            with (
                patch.object(convert_orders, "STATE_ROOT", root),
                patch.object(
                    convert_orders.subprocess,
                    "run",
                    side_effect=complete_sync,
                ) as run,
            ):
                first = convert_orders._prepare_official_mapping(
                    _args(), sys.executable, root, root / "catalog.csv", ()
                )
                second = convert_orders._prepare_official_mapping(
                    _args(), sys.executable, root, root / "catalog.csv", ()
                )

        self.assertEqual(run.call_count, 2)
        self.assertIsNotNone(first[0])
        self.assertIsNotNone(second[0])
        self.assertNotEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])

    def test_explicit_snapshot_without_sync_receipt_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot_path = root / "native-snapshot.json"
            snapshot_path.write_text('{"source":"old"}', encoding="utf-8")
            with patch.object(convert_orders, "STATE_ROOT", root / "state"):
                snapshot, receipt, error = convert_orders._prepare_official_mapping(
                    _args(snapshot=snapshot_path),
                    sys.executable,
                    root,
                    root / "catalog.csv",
                    (),
                )

        self.assertIsNone(snapshot)
        self.assertIsNone(receipt)
        self.assertIn("缺少当前任务同步凭证", error)

    def test_explicit_sync_receipt_can_only_bind_one_conversion_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot_path = root / "native-snapshot.json"
            snapshot_path.write_text('{"source":"fresh"}', encoding="utf-8")
            receipt_path = root / "native-receipt.json"
            receipt_path.write_text(
                json.dumps({"receipt_id": "a" * 32}),
                encoding="utf-8",
            )
            args = _args(snapshot=snapshot_path, receipt=receipt_path)
            with patch.object(convert_orders, "STATE_ROOT", root / "state"):
                first = convert_orders._prepare_official_mapping(
                    args,
                    sys.executable,
                    root,
                    root / "catalog.csv",
                    (),
                )
                second = convert_orders._prepare_official_mapping(
                    args,
                    sys.executable,
                    root,
                    root / "catalog.csv",
                    (),
                )

        self.assertIsNotNone(first[0])
        self.assertIsNotNone(first[1])
        self.assertIsNone(second[0])
        self.assertIn("已被另一个转换任务使用", second[2])


if __name__ == "__main__":
    unittest.main()
