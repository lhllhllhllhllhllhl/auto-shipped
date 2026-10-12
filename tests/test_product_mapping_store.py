from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import (
    CURRENT_BATCH_MAPPING_CONFIRMATION,
    PERSIST_MAPPING_CONFIRMATION,
    ProductCatalog,
    ProductMappingStoreError,
    ProductRecord,
    save_confirmed_mapping,
)


class ProductMappingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ProductCatalog(
            [ProductRecord("P1", "测试商品", "S1", "测试规格", 100, "测试仓")]
        )

    def test_requires_explicit_persistence_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProductMappingStoreError):
                save_confirmed_mapping(
                    Path(tmp) / "overlay.json",
                    self.catalog,
                    source_profile_id="company_a",
                    identifier_type="product_code",
                    source_value="EXT-1",
                    product_code="P1",
                    spec_code="S1",
                    confirmation_token="",
                )

    def test_saved_local_overlay_is_reusable_by_source_company(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            overlay = root / "overlay.json"
            saved = save_confirmed_mapping(
                overlay,
                self.catalog,
                source_profile_id="company_a",
                identifier_type="product_code",
                source_value="EXT-1",
                product_code="P1",
                spec_code="S1",
                confirmation_token=PERSIST_MAPPING_CONFIRMATION,
            )
            document = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], "2.0")
            self.assertEqual(document["mappings"][0]["source_profile_id"], "company_a")
            self.assertNotIn("recipient", document["mappings"][0])

    def test_spec_only_mapping_can_be_persisted_in_universal_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_confirmed_mapping(
                Path(tmp) / "overlay.json",
                self.catalog,
                source_profile_id="sam_order_v1",
                identifier_type="product_code",
                source_value="",
                source_spec="SWSH8096",
                product_code="P1",
                spec_code="S1",
                confirmation_token=PERSIST_MAPPING_CONFIRMATION,
            )
            record = json.loads(saved.read_text(encoding="utf-8"))["mappings"][0]
            self.assertEqual(record["identifier_type"], "product_code")
            self.assertEqual(record["source_value"], "")
            self.assertEqual(record["source_spec"], "SWSH8096")

    def test_blank_target_spec_is_canonicalized_for_single_variant_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_confirmed_mapping(
                Path(tmp) / "overlay.json",
                self.catalog,
                source_profile_id="company_a",
                identifier_type="product_name",
                source_value="测试商品别名",
                product_code="P1",
                spec_code="",
                confirmation_token=PERSIST_MAPPING_CONFIRMATION,
            )
            record = json.loads(saved.read_text(encoding="utf-8"))["mappings"][0]
            self.assertEqual(record["product_code"], "P1")
            self.assertEqual(record["spec_code"], "S1")
            self.assertEqual(record["reuse_scope"], "future")

    def test_current_batch_mapping_is_bound_to_one_source_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fingerprint = "sha256:" + "a" * 64
            saved = save_confirmed_mapping(
                Path(tmp) / "batch-overlay.json",
                self.catalog,
                source_profile_id="company_a",
                identifier_type="product_code",
                source_value="EXT-1",
                product_code="P1",
                spec_code="S1",
                reuse_scope="current_batch",
                source_fingerprint=fingerprint,
                confirmation_token=CURRENT_BATCH_MAPPING_CONFIRMATION,
            )
            record = json.loads(saved.read_text(encoding="utf-8"))["mappings"][0]
            self.assertEqual(record["reuse_scope"], "current_batch")
            self.assertEqual(record["mapping_authority"], "user_batch_confirmation")
            self.assertEqual(record["source_fingerprint"], fingerprint)

    def test_current_batch_mapping_rejects_missing_source_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProductMappingStoreError):
                save_confirmed_mapping(
                    Path(tmp) / "batch-overlay.json",
                    self.catalog,
                    source_profile_id="company_a",
                    identifier_type="product_code",
                    source_value="EXT-1",
                    product_code="P1",
                    spec_code="S1",
                    reuse_scope="current_batch",
                    confirmation_token=CURRENT_BATCH_MAPPING_CONFIRMATION,
                )


if __name__ == "__main__":
    unittest.main()
