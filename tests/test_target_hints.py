from __future__ import annotations

import unittest

from auto_shipped.catalog import ProductCatalog, ProductRecord, resolve_target_hint
from auto_shipped.services import (
    ProductDecisionError,
    resolve_product_mapping_decision,
)


def catalog() -> ProductCatalog:
    return ProductCatalog(
        [
            ProductRecord(
                product_code="KKAW0030",
                product_name="kikulon蓝色硬款搓澡巾",
                spec_code="4548404200030",
                spec_name="蓝色【硬款】",
                weight_grams=None,
                default_warehouse="测试仓",
            ),
            ProductRecord(
                product_code="MULTI",
                product_name="多规格商品",
                spec_code="SPEC-A",
                spec_name="A规格",
                weight_grams=None,
                default_warehouse="测试仓",
            ),
            ProductRecord(
                product_code="MULTI",
                product_name="多规格商品",
                spec_code="SPEC-B",
                spec_name="B规格",
                weight_grams=None,
                default_warehouse="测试仓",
            ),
        ]
    )


def decision_document(*, identifier_type: str = "product_name") -> dict:
    return {
        "decision_bundle": {
            "items": [
                {
                    "decision_id": "D-123456789abc",
                    "code": "CONFIRM_GUANYI_PRODUCT_MAPPING",
                    "proposed_value": {
                        "company_id": "self_operated",
                        "source_profile_id": "taojuzi_taobao_text_v1",
                        "source_identifier_type": identifier_type,
                        "source_product_code": "SRC-001",
                        "source_product_name": "蓝色硬款搓澡巾",
                        "source_spec": "蓝色硬款",
                        "source_fingerprint": "sha256:" + "a" * 64,
                    },
                }
            ]
        }
    }


class TargetHintTests(unittest.TestCase):
    def test_exact_spec_code_is_resolved_without_fuzzy_matching(self) -> None:
        result = resolve_target_hint(
            catalog(), "4548404200030", declared_type="spec_code"
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.match_method, "exact_spec_code")
        assert result.product is not None
        self.assertEqual(result.product.product_code, "KKAW0030")

    def test_untyped_code_searches_target_identifiers(self) -> None:
        result = resolve_target_hint(catalog(), "这个规格代码是：4548404200030")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.match_method, "exact_auto_identifier")

    def test_product_code_with_multiple_specs_is_ambiguous(self) -> None:
        result = resolve_target_hint(
            catalog(), "MULTI", declared_type="product_code"
        )

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(len(result.candidates), 2)

    def test_spec_code_disambiguates_product_code_when_both_are_supplied(self) -> None:
        result = resolve_target_hint(catalog(), "MULTI / SPEC-B")

        self.assertEqual(result.status, "resolved")
        assert result.product is not None
        self.assertEqual(result.product.spec_code, "SPEC-B")

    def test_product_and_spec_names_can_be_supplied_together(self) -> None:
        result = resolve_target_hint(catalog(), "多规格商品 / B规格")

        self.assertEqual(result.status, "resolved")
        assert result.product is not None
        self.assertEqual(result.product.spec_code, "SPEC-B")

    def test_decision_resolution_keeps_original_source_identity(self) -> None:
        result = resolve_product_mapping_decision(
            decision_document(),
            catalog(),
            decision_id="D-123456789abc",
            target_hint="4548404200030",
            hint_type="spec_code",
            reuse_scope="future",
            mapping_scope="global",
        ).to_dict()

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["source_identity"]["source_value"], "蓝色硬款搓澡巾"
        )
        self.assertEqual(result["mapping_record"]["company_id"], "*")
        self.assertEqual(result["mapping_record"]["source_profile_id"], "*")
        self.assertEqual(result["mapping_record"]["product_code"], "KKAW0030")
        self.assertTrue(
            result["safety"]["user_target_hint_never_rewrites_source"]
        )

    def test_global_scope_rejects_source_codes(self) -> None:
        with self.assertRaisesRegex(ProductDecisionError, "全局复用只允许"):
            resolve_product_mapping_decision(
                decision_document(identifier_type="product_code"),
                catalog(),
                decision_id="D-123456789abc",
                target_hint="4548404200030",
                reuse_scope="future",
                mapping_scope="global",
            )


if __name__ == "__main__":
    unittest.main()
