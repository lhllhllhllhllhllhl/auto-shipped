from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "skill" / "shangyu-order-converter" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from convert_orders import _verify_text_intake_receipts  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TextIntakeReceiptTests(unittest.TestCase):
    def test_receipt_binds_source_plan_route_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            profiles = project / "config" / "source_profiles"
            profiles.mkdir(parents=True)
            profile = profiles / "taobao.json"
            profile.write_text(
                json.dumps(
                    {
                        "profile_id": "taojuzi_taobao_text_v1",
                        "company_id": "self_operated",
                    }
                ),
                encoding="utf-8",
            )
            source = root / "orders.xlsx"
            source.write_bytes(b"deterministic-text-order-workbook")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "sheet_name": "TextOrders",
                        "company_id": "self_operated",
                        "rule_source_profile_id": "taojuzi_taobao_text_v1",
                    }
                ),
                encoding="utf-8",
            )
            receipt = root / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schema_version": "text-intake-receipt/1.0",
                        "source_kind": "TextOrders",
                        "privacy": "no_recipient_pii",
                        "source": {
                            "file_name": source.name,
                            "sha256": _sha256(source),
                        },
                        "adaptive_plan": {
                            "file_name": plan.name,
                            "sha256": _sha256(plan),
                        },
                        "route": {
                            "company_id": "self_operated",
                            "rule_source_profile_id": "taojuzi_taobao_text_v1",
                            "source_profile_sha256": _sha256(profile),
                        },
                    }
                ),
                encoding="utf-8",
            )
            key = str(source.resolve())
            valid = _verify_text_intake_receipts(
                project_root=project,
                sources=[source.resolve()],
                company_hints={key: "self_operated"},
                adaptive_plan_hints={key: str(plan)},
                receipt_hints={key: str(receipt)},
            )
            self.assertIsNone(valid)

            source.write_bytes(b"tampered")
            mismatch = _verify_text_intake_receipts(
                project_root=project,
                sources=[source.resolve()],
                company_hints={key: "self_operated"},
                adaptive_plan_hints={key: str(plan)},
                receipt_hints={key: str(receipt)},
            )
            self.assertIn("TEXT_ORDER_INTAKE_RECEIPT_SOURCE_MISMATCH", mismatch or "")

    def test_text_orders_without_receipt_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "orders.xlsx"
            source.write_bytes(b"workbook")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({"sheet_name": "TextOrders"}),
                encoding="utf-8",
            )
            key = str(source.resolve())
            error = _verify_text_intake_receipts(
                project_root=root,
                sources=[source.resolve()],
                company_hints={},
                adaptive_plan_hints={key: str(plan)},
                receipt_hints={},
            )
            self.assertIn("TEXT_ORDER_INTAKE_RECEIPT_REQUIRED", error or "")


if __name__ == "__main__":
    unittest.main()
