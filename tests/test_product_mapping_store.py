from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import (
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


if __name__ == "__main__":
    unittest.main()
