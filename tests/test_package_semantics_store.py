from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import (
    PACKAGE_SEMANTICS_REUSE_CONFIRMATION,
    PackageSemanticsStoreError,
    export_package_semantics_proposals,
    list_package_semantics_proposals,
    save_package_semantics_proposal,
)


class PackageSemanticsStoreTests(unittest.TestCase):
    def test_reusable_rule_is_saved_separately_and_exported_without_order_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = save_package_semantics_proposal(
                root / "pending",
                company_id="company_x",
                source_profile_id="company_x_text_v1",
                product_family="yellow_corn",
                source_expression="根",
                semantic_type="pack_content_count",
                quantity_strategy="divide_by_sticks_per_target_unit",
                sticks_per_target_unit=8,
                confirmation_token=PACKAGE_SEMANTICS_REUSE_CONFIRMATION,
            )
            self.assertFalse(saved["reused"])
            self.assertEqual(len(list_package_semantics_proposals(root / "pending")), 1)

            exported = export_package_semantics_proposals(
                root / "pending",
                root / "exports" / "package-semantics.json",
            )
            self.assertEqual(exported["proposal_count"], 1)
            payload = json.loads(Path(exported["output"]).read_text(encoding="utf-8"))
            rendered = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("recipient_name", rendered.casefold())
            self.assertNotIn("recipient_contact", rendered.casefold())
            self.assertNotIn("raw_address", rendered.casefold())
            self.assertEqual(payload["proposals"][0]["company_id"], "company_x")

    def test_same_proposal_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            arguments = dict(
                company_id="rongzhida",
                source_profile_id="rongzhida_text_v1",
                product_family="yellow_corn",
                source_expression="根",
                semantic_type="single_stick_packaged",
                quantity_strategy="same_as_source",
                confirmation_token=PACKAGE_SEMANTICS_REUSE_CONFIRMATION,
            )
            first = save_package_semantics_proposal(temporary, **arguments)
            second = save_package_semantics_proposal(temporary, **arguments)
            self.assertEqual(first["proposal_id"], second["proposal_id"])
            self.assertTrue(second["reused"])

    def test_missing_future_reuse_confirmation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(PackageSemanticsStoreError):
                save_package_semantics_proposal(
                    temporary,
                    company_id="company_x",
                    source_profile_id="company_x_text_v1",
                    product_family="yellow_corn",
                    source_expression="根",
                    semantic_type="single_stick_packaged",
                    quantity_strategy="same_as_source",
                    confirmation_token="CONFIRM_THIS_ORDER_ONLY",
                )


if __name__ == "__main__":
    unittest.main()
