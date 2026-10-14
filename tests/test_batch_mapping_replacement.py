from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import (
    BatchMappingReplacementError,
    ProductCatalog,
    ProductRecord,
    save_batch_mapping_replacement,
    validate_batch_mapping_replacement_document,
)


FINGERPRINT = "sha256:" + "a" * 64


def catalog(*, replacements: list[dict[str, str]] | None = None) -> ProductCatalog:
    records = [
        ProductRecord("P1", "旧商品", "S1", "旧规格", None, ""),
        ProductRecord("P2", "新商品", "S2", "新规格", None, ""),
    ]
    mappings = [
        {
            "mapping_id": "official-1",
            "source_profile_id": "profile-a",
            "identifier_type": "product_name",
            "source_value": "来源商品",
            "source_spec": "蓝色",
            "product_code": "P1",
            "spec_code": "S1",
            "confirmed": True,
            "reuse_scope": "future",
            "mapping_authority": "feishu_official",
        }
    ]
    return ProductCatalog(records, mappings, batch_mapping_replacements=replacements)


class BatchMappingReplacementTests(unittest.TestCase):
    def test_duplicate_source_key_and_fingerprint_is_rejected(self) -> None:
        record = {
            "replacement_id": "replacement-1",
            "company_id": "company-a",
            "source_profile_id": "profile-a",
            "identifier_type": "product_name",
            "source_value": "来源商品",
            "source_spec": "蓝色",
            "expected_product_code": "P1",
            "expected_spec_code": "S1",
            "replacement_product_code": "P2",
            "replacement_spec_code": "S2",
            "source_fingerprint": FINGERPRINT,
            "scope": "current_batch",
            "confirmed_by": "ou-user",
            "confirmed_at": "2026-10-15T00:00:00+00:00",
            "reason": "用户否决旧映射",
        }
        with self.assertRaises(BatchMappingReplacementError):
            validate_batch_mapping_replacement_document(
                {
                    "schema_version": "1.0",
                    "source": "current_batch_official_mapping_replacements",
                    "replacements": [record, {**record, "replacement_id": "replacement-2"}],
                }
            )

    def test_replacement_document_is_loaded_separately_from_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_csv = root / "catalog.csv"
            catalog_csv.write_text(
                "商品代码,商品名称,规格代码,规格名称,重量,默认仓库\n"
                "P1,旧商品,S1,旧规格,,测试仓\n"
                "P2,新商品,S2,新规格,,测试仓\n",
                encoding="gb18030",
            )
            mappings = root / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "mapping_id": "official-1",
                                "source_profile_id": "profile-a",
                                "identifier_type": "product_name",
                                "source_value": "来源商品",
                                "source_spec": "蓝色",
                                "product_code": "P1",
                                "spec_code": "S1",
                                "confirmed": True,
                                "reuse_scope": "future",
                                "mapping_authority": "feishu_official",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            replacements = root / "replacements.json"
            replacements.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "source": "current_batch_official_mapping_replacements",
                        "replacements": [
                            {
                                "replacement_id": "replacement-1",
                                "company_id": "company-a",
                                "source_profile_id": "profile-a",
                                "identifier_type": "product_name",
                                "source_value": "来源商品",
                                "source_spec": "蓝色",
                                "expected_product_code": "P1",
                                "expected_spec_code": "S1",
                                "replacement_product_code": "P2",
                                "replacement_spec_code": "S2",
                                "source_fingerprint": FINGERPRINT,
                                "scope": "current_batch",
                                "confirmed_by": "ou-user",
                                "confirmed_at": "2026-10-15T00:00:00+00:00",
                                "reason": "用户否决旧映射",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            loaded = ProductCatalog.from_files(
                catalog_csv,
                mappings,
                mapping_replacement_paths=(replacements,),
            )
            result = loaded.resolve(
                "",
                source_profile_id="profile-a",
                source_product_name="来源商品",
                source_spec="蓝色",
                source_fingerprint=FINGERPRINT,
            )

            self.assertEqual(result.status, "confirmed")
            self.assertEqual(result.product.product_code, "P2")

    def test_exact_source_fingerprint_replaces_only_current_batch(self) -> None:
        replacement = {
            "replacement_id": "replacement-1",
            "company_id": "company-a",
            "source_profile_id": "profile-a",
            "identifier_type": "product_name",
            "source_value": "来源商品",
            "source_spec": "蓝色",
            "expected_product_code": "P1",
            "expected_spec_code": "S1",
            "replacement_product_code": "P2",
            "replacement_spec_code": "S2",
            "source_fingerprint": FINGERPRINT,
            "scope": "current_batch",
            "confirmed_by": "ou-user",
            "confirmed_at": "2026-10-15T00:00:00+00:00",
            "reason": "用户否决旧映射",
        }
        product_catalog = catalog(replacements=[replacement])

        current = product_catalog.resolve(
            "",
            source_profile_id="profile-a",
            source_product_name="来源商品",
            source_spec="蓝色",
            source_fingerprint=FINGERPRINT,
        )
        later = product_catalog.resolve(
            "",
            source_profile_id="profile-a",
            source_product_name="来源商品",
            source_spec="蓝色",
            source_fingerprint="sha256:" + "b" * 64,
        )

        self.assertEqual(current.status, "confirmed")
        self.assertEqual(current.product.product_code, "P2")
        self.assertEqual(
            current.match_method, "current_batch_official_mapping_replacement"
        )
        self.assertEqual(later.product.product_code, "P1")
        self.assertEqual(later.match_method, "confirmed_name_alias_name_spec")

    def test_save_requires_expected_official_target_and_separate_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "replacements.json"
            saved = save_batch_mapping_replacement(
                output,
                catalog(),
                company_id="company-a",
                source_profile_id="profile-a",
                identifier_type="product_name",
                source_value="来源商品",
                source_spec="蓝色",
                expected_product_code="P1",
                expected_spec_code="S1",
                replacement_product_code="P2",
                replacement_spec_code="S2",
                source_fingerprint=FINGERPRINT,
                reason="用户否决旧映射",
                confirmed_by="ou-user",
                confirmation_token="CONFIRM_REPLACE_OFFICIAL_MAPPING_FOR_CURRENT_BATCH",
            )

            document = saved.read_text(encoding="utf-8")
            self.assertIn("current_batch_official_mapping_replacements", document)
            self.assertIn('"expected_product_code": "P1"', document)
            self.assertNotIn("feishu_official_mapping_repository", document)


if __name__ == "__main__":
    unittest.main()
