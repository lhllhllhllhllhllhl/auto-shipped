from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.services.delivery_receipt import (
    DeliveryReceiptError,
    verify_delivery_receipt,
    write_delivery_receipt,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DeliveryReceiptTests(unittest.TestCase):
    def _ready_artifacts(self, root: Path) -> tuple[Path, Path, Path, Path, dict]:
        workbook = root / "orders.xlsx"
        workbook.write_bytes(b"deterministic workbook")
        receipt = root / "orders_receipt.json"
        workflow = {
            "workflow_id": "0123456789abcdef",
            "status": "complete",
            "stages": [
                {
                    "stage_id": "output_render_readback",
                    "status": "completed",
                    "output_sha256": _sha256(workbook),
                }
            ],
        }
        manifest = root / "orders_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "status": "ready",
                    "artifact": {"sha256": _sha256(workbook)},
                }
            ),
            encoding="utf-8",
        )
        report = root / "orders_report.json"
        report.write_text(
            json.dumps(
                {
                    "status": "ready",
                    "delivery_receipt": str(receipt),
                    "upload_manifest": str(manifest),
                    "workflow_execution": workflow,
                }
            ),
            encoding="utf-8",
        )
        return workbook, report, manifest, receipt, workflow

    def test_ready_receipt_binds_workbook_report_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook, report, manifest, receipt, workflow = self._ready_artifacts(root)
            write_delivery_receipt(
                receipt,
                delivery_status="ready",
                workbook_path=workbook,
                report_path=report,
                upload_manifest_path=manifest,
                workflow_execution=workflow,
            )

            verified = verify_delivery_receipt(receipt)

        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["delivery_status"], "ready")
        self.assertEqual(verified["workflow_id"], "0123456789abcdef")

    def test_receipt_rejects_workbook_modified_after_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook, report, manifest, receipt, workflow = self._ready_artifacts(root)
            write_delivery_receipt(
                receipt,
                delivery_status="ready",
                workbook_path=workbook,
                report_path=report,
                upload_manifest_path=manifest,
                workflow_execution=workflow,
            )
            workbook.write_bytes(b"manually changed")

            with self.assertRaises(DeliveryReceiptError) as raised:
                verify_delivery_receipt(receipt)

        self.assertEqual(
            raised.exception.code,
            "DELIVERY_RECEIPT_ARTIFACT_CHANGED",
        )

    def test_preview_receipt_forbids_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "preview.xlsx"
            workbook.write_bytes(b"preview workbook")
            receipt = root / "preview_receipt.json"
            workflow = {
                "workflow_id": "fedcba9876543210",
                "status": "complete",
                "stages": [
                    {
                        "stage_id": "output_render_readback",
                        "status": "completed",
                        "output_sha256": _sha256(workbook),
                    }
                ],
            }
            report = root / "preview_report.json"
            report.write_text(
                json.dumps(
                    {
                        "status": "preview_ready",
                        "delivery_receipt": str(receipt),
                        "upload_manifest": None,
                        "workflow_execution": workflow,
                    }
                ),
                encoding="utf-8",
            )
            write_delivery_receipt(
                receipt,
                delivery_status="preview_ready",
                workbook_path=workbook,
                report_path=report,
                upload_manifest_path=None,
                workflow_execution=workflow,
            )

            verified = verify_delivery_receipt(receipt)

        self.assertEqual(verified["delivery_status"], "preview_ready")
        self.assertIsNone(verified["upload_manifest"])


if __name__ == "__main__":
    unittest.main()
