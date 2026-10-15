from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import ProductCatalog, ProductRecord
from auto_shipped.catalog import BatchItemExpansionError, save_batch_item_expansion
from auto_shipped.integrations.feishu import (
    ItemExpansionRegistryError,
    build_item_expansion_rule_key,
    normalize_item_expansion_rules,
)
from auto_shipped.domain import ParsedItem
from auto_shipped.platforms.guanyi.item_expansion import expand_source_item


def catalog() -> ProductCatalog:
    return ProductCatalog(
        [
            ProductRecord("P1", "商品一", "S1", "规格一", None, "上海仓"),
            ProductRecord("P2", "商品二", "S2", "规格二", None, "上海仓"),
        ]
    )


def rows() -> list[dict[str, str]]:
    rule_key = build_item_expansion_rule_key(
        company_id="sam",
        source_profile_id="sam_order_v1",
        identifier_type="product_code",
        source_value="SOURCE-1",
        source_spec="8+1",
        target_platform="guanyi",
    )
    common = {
        "rule_id": "sam-source-1-bundle",
        "rule_key": rule_key,
        "company_id": "sam",
        "source_profile_id": "sam_order_v1",
        "identifier_type": "product_code",
        "source_value": "SOURCE-1",
        "source_spec": "8+1",
        "target_platform": "guanyi",
        "status": "active",
        "rule_version": "3",
        "confirmed_by": "ou-test",
        "confirmed_at": "2026-10-15T10:00:00+08:00",
        "evidence": "用户确认以后沿用",
    }
    return [
        {
            **common,
            "component_id": "component-1",
            "component_index": "1",
            "product_code": "P1",
            "spec_code": "S1",
            "quantity_multiplier": "1",
        },
        {
            **common,
            "component_id": "component-2",
            "component_index": "2",
            "product_code": "P2",
            "spec_code": "S2",
            "quantity_multiplier": "2",
        },
    ]


class FeishuItemExpansionTests(unittest.TestCase):
    def test_active_component_rows_become_one_future_reusable_rule(self) -> None:
        normalized = normalize_item_expansion_rules(
            rows(), catalog=catalog(), repository_revision="3"
        )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["reuse_scope"], "future")
        self.assertEqual(normalized[0]["mapping_authority"], "feishu_official")
        self.assertEqual(
            [
                (
                    item["product_code"],
                    item["spec_code"],
                    item["quantity_multiplier"],
                )
                for item in normalized[0]["components"]
            ],
            [("P1", "S1", 1.0), ("P2", "S2", 2.0)],
        )

    def test_rule_requires_at_least_two_distinct_targets(self) -> None:
        with self.assertRaises(ItemExpansionRegistryError):
            normalize_item_expansion_rules(
                rows()[:1], catalog=catalog(), repository_revision="3"
            )

        duplicate = rows()
        duplicate[1]["product_code"] = "P1"
        duplicate[1]["spec_code"] = "S1"
        with self.assertRaises(ItemExpansionRegistryError):
            normalize_item_expansion_rules(
                duplicate, catalog=catalog(), repository_revision="3"
            )

    def test_component_indices_and_identity_must_be_consistent(self) -> None:
        non_contiguous = rows()
        non_contiguous[1]["component_index"] = "3"
        with self.assertRaises(ItemExpansionRegistryError):
            normalize_item_expansion_rules(
                non_contiguous, catalog=catalog(), repository_revision="3"
            )

        mixed_identity = rows()
        mixed_identity[1]["source_value"] = "OTHER"
        with self.assertRaises(ItemExpansionRegistryError):
            normalize_item_expansion_rules(
                mixed_identity, catalog=catalog(), repository_revision="3"
            )

    def test_central_registered_rule_expands_without_local_static_rule(self) -> None:
        registered = normalize_item_expansion_rules(
            rows(), catalog=catalog(), repository_revision="3"
        )
        product_catalog = ProductCatalog(
            list(catalog().records),
            item_expansion_rules=registered,
        )
        item = ParsedItem(
            source_product_code="SOURCE-1",
            source_product_name="来源组合商品",
            source_spec="8+1",
            source_display_spec="8根+1根",
            quantity=2,
            source_line_no=2,
        )

        result = expand_source_item(
            item,
            product_catalog,
            {"item_expansion_policy": {"strategy": "confirmed_source_item_expansions"}},
            company_id="sam",
            source_profile_id="sam_order_v1",
            registered_rules=product_catalog.item_expansion_rules,
        )
        wrong_company = expand_source_item(
            item,
            product_catalog,
            {"item_expansion_policy": {"strategy": "confirmed_source_item_expansions"}},
            company_id="ndd",
            source_profile_id="sam_order_v1",
            registered_rules=product_catalog.item_expansion_rules,
        )

        self.assertTrue(result.applied)
        self.assertFalse(result.failed)
        self.assertEqual(
            [(line.product.product_code, line.quantity) for line in result.lines],
            [("P1", 2.0), ("P2", 4.0)],
        )
        self.assertEqual(
            wrong_company.error_code, "GUANYI_ITEM_EXPANSION_UNCONFIRMED"
        )

    def test_unregistered_explicit_bundle_fails_closed(self) -> None:
        item = ParsedItem(
            source_product_code="NEW-BUNDLE",
            source_product_name="新品加送1根",
            source_spec="8+1",
            source_display_spec="8根+1根",
            quantity=1,
            source_line_no=2,
        )

        result = expand_source_item(
            item,
            catalog(),
            {"item_expansion_policy": {"strategy": "confirmed_source_item_expansions"}},
            company_id="sam",
            source_profile_id="sam_order_v1",
        )

        self.assertTrue(result.applied)
        self.assertEqual(result.error_code, "GUANYI_ITEM_EXPANSION_UNCONFIRMED")

    def test_current_batch_overlay_is_bound_to_exact_source_fingerprint(self) -> None:
        fingerprint = f"sha256:{'a' * 64}"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "item-expansion.json"
            save_batch_item_expansion(
                path,
                catalog=catalog(),
                company_id="sam",
                source_profile_id="sam_order_v1",
                identifier_type="product_code",
                source_value="SOURCE-1",
                source_spec="8+1",
                target_platform="guanyi",
                source_fingerprint=fingerprint,
                components=[
                    {"product_code": "P1", "spec_code": "S1", "quantity_multiplier": 1},
                    {"product_code": "P2", "spec_code": "S2", "quantity_multiplier": 1},
                ],
                evidence="用户确认仅本批使用",
                confirmation_token="CONFIRM_USE_ITEM_EXPANSION_FOR_CURRENT_BATCH",
            )
            rule = json.loads(path.read_text(encoding="utf-8"))["item_expansions"][0]

        item = ParsedItem(
            source_product_code="SOURCE-1",
            source_product_name="来源组合商品",
            source_spec="8+1",
            source_display_spec="8+1",
            quantity=1,
            source_line_no=2,
        )
        matched = expand_source_item(
            item,
            catalog(),
            {"item_expansion_policy": {"strategy": "confirmed_source_item_expansions"}},
            company_id="sam",
            source_profile_id="sam_order_v1",
            source_fingerprint=fingerprint,
            registered_rules=[rule],
        )
        other_file = expand_source_item(
            item,
            catalog(),
            {"item_expansion_policy": {"strategy": "confirmed_source_item_expansions"}},
            source_profile_id="sam_order_v1",
            source_fingerprint=f"sha256:{'b' * 64}",
            registered_rules=[rule],
        )

        self.assertFalse(matched.failed)
        self.assertEqual(len(matched.lines), 2)
        self.assertEqual(other_file.error_code, "GUANYI_ITEM_EXPANSION_UNCONFIRMED")

    def test_current_batch_overlay_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(BatchItemExpansionError):
                save_batch_item_expansion(
                    Path(directory) / "item-expansion.json",
                    catalog=catalog(),
                    company_id="sam",
                    source_profile_id="sam_order_v1",
                    identifier_type="product_code",
                    source_value="SOURCE-1",
                    source_spec="8+1",
                    target_platform="guanyi",
                    source_fingerprint=f"sha256:{'a' * 64}",
                    components=[
                        {"product_code": "P1", "spec_code": "S1", "quantity_multiplier": 1},
                        {"product_code": "P2", "spec_code": "S2", "quantity_multiplier": 1},
                    ],
                    evidence="用户确认仅本批使用",
                    confirmation_token="",
                )


if __name__ == "__main__":
    unittest.main()
