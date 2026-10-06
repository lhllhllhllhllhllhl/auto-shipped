from __future__ import annotations

import unittest
from datetime import date

from auto_shipped.domain import (
    ParsedItem,
    ParsedOrder,
    ParsedRecipient,
    ParsedShipmentFacts,
    ParsedSource,
)
from auto_shipped.platforms.guanyi.order_policies import (
    GuanyiOrderPolicyError,
    ResolvedOrderItem,
    compose_seller_remark,
    select_buyer_member,
    select_logistics_carrier,
)


def order(carrier: str = "") -> ParsedOrder:
    return ParsedOrder(
        record_key="order-1",
        source=ParsedSource(
            profile_id="tiantian_warehouse_v2",
            source_label="恬田代发仓",
            order_type="warehouse_order",
            source_order_no="SRC-001",
            file_name="sample.xlsx",
            file_fingerprint="sha256:test",
            sheet_name="仓库订单",
            row_numbers=(2,),
        ),
        recipient=ParsedRecipient(name="测试用户", raw_address="测试地址"),
        items=[ParsedItem(quantity=1, source_product_code="EXT-10")],
        shipment_facts=(
            ParsedShipmentFacts(carrier_name=carrier) if carrier else None
        ),
    )


def rules() -> dict:
    return {
        "logistics_policy": {
            "strategy": "source_override_then_confirmed_packaging_rules",
            "source_carrier_priority": True,
            "source_carrier_aliases": {
                "客户指定物流": "客户指定物流",
                "韵达": "韵达快递",
            },
            "default_carrier": "韵达快递",
            "heavy_carrier": "中通快递（重货）",
            "heavy_conditions": [
                {
                    "match": {"product_codes": ["JTW8E1"]},
                    "minimum_quantity": 2,
                },
                {
                    "match": {
                        "product_name_contains_all": ["玉米"],
                        "any_text_contains": ["10根", "10棒"],
                    },
                    "minimum_quantity": 1,
                },
            ],
        }
    }


class GuanyiOrderPolicyTests(unittest.TestCase):
    def test_buyer_member_is_fixed_for_normal_sources(self) -> None:
        self.assertEqual(
            select_buyer_member(
                order(),
                {"buyer_member_policy": {"strategy": "fixed", "value": "张"}},
            ),
            "张",
        )

    def test_sam_group_number_overrides_default_buyer_member(self) -> None:
        sam_order = order()
        sam_order.source_extensions["sam_group_number"] = "841"
        policy = {
            "buyer_member_policy": {
                "strategy": "source_extension_or_default",
                "source_extension_field": "sam_group_number",
                "default_value": "张",
            }
        }
        self.assertEqual(select_buyer_member(sam_order, policy), "841")
        sam_order.source_extensions.clear()
        self.assertEqual(select_buyer_member(sam_order, policy), "张")

    def test_sam_buyer_member_uses_product_type_date_and_group_number(self) -> None:
        sam_order = order()
        sam_order.source_extensions["sam_group_number"] = "841"
        policy = {
            "buyer_member_policy": {
                "strategy": "sam_product_type_date_group",
                "platform_prefix": "sam",
                "group_number_field": "sam_group_number",
                "group_separator": "_",
                "date_format": "%y%m%d",
                "timezone": "Asia/Shanghai",
                "product_type_rules": [
                    {
                        "type_code": "YM",
                        "match": {"product_name_contains_all": ["玉米"]},
                        "confirmed": True,
                    },
                    {
                        "type_code": "SH",
                        "match": {
                            "product_name_contains_all": ["手套"],
                            "any_text_contains": ["尚和", "尚合"],
                        },
                        "confirmed": True,
                    },
                ],
            }
        }
        items = [
            ResolvedOrderItem(
                2,
                "JTBN1E1-EH",
                "6974768564842",
                "光明满元气 白糯玉米8棒家庭装",
                "8袋/箱",
            ),
            ResolvedOrderItem(
                2,
                "GMBN1",
                "6974768564804",
                "光明满元气 白糯玉米单根装",
                "50根/箱",
            ),
        ]
        self.assertEqual(
            select_buyer_member(
                sam_order,
                policy,
                items,
                business_date=date(2026, 9, 20),
            ),
            "samYM260920_841",
        )

    def test_sam_buyer_member_blocks_unknown_product_type(self) -> None:
        sam_order = order()
        sam_order.source_extensions["sam_group_number"] = "841"
        policy = {
            "buyer_member_policy": {
                "strategy": "sam_product_type_date_group",
                "platform_prefix": "sam",
                "group_number_field": "sam_group_number",
                "product_type_rules": [],
            }
        }
        with self.assertRaises(GuanyiOrderPolicyError):
            select_buyer_member(
                sam_order,
                policy,
                [ResolvedOrderItem(1, "NEW", "SPEC", "未知商品", "")],
                business_date=date(2026, 9, 20),
            )

    def test_confirmed_ten_stick_gift_box_is_heavy(self) -> None:
        resolved = [
            ResolvedOrderItem(
                quantity=1,
                product_code="CORN10",
                spec_code="SPEC10",
                product_name="光明满元气玉米礼盒10根装",
                spec_name="10根/盒",
            )
        ]
        self.assertEqual(
            select_logistics_carrier(order(), resolved, rules()),
            "中通快递（重货）",
        )

    def test_source_carrier_wins_before_packaging_rules(self) -> None:
        resolved = [
            ResolvedOrderItem(2, "JTW8E1", "S1", "玉米8棒家庭装", "8袋/箱")
        ]
        self.assertEqual(
            select_logistics_carrier(order("客户指定物流"), resolved, rules()),
            "客户指定物流",
        )

    def test_source_carrier_alias_is_normalized(self) -> None:
        resolved = [ResolvedOrderItem(1, "OTHER", "S1", "普通商品", "")]
        self.assertEqual(
            select_logistics_carrier(order("韵达"), resolved, rules()),
            "韵达快递",
        )

    def test_unknown_source_carrier_blocks(self) -> None:
        resolved = [ResolvedOrderItem(1, "OTHER", "S1", "普通商品", "")]
        with self.assertRaises(GuanyiOrderPolicyError):
            select_logistics_carrier(order("随便快递"), resolved, rules())

    def test_source_carrier_priority_switch_changes_heavy_order_result(self) -> None:
        resolved = [
            ResolvedOrderItem(2, "JTW8E1", "S1", "玉米8棒家庭装", "8袋/箱")
        ]
        configured = rules()
        configured["logistics_policy"]["source_carrier_priority"] = False
        configured["logistics_policy"][
            "strategy"
        ] = "confirmed_packaging_rules_then_source_fallback"
        self.assertEqual(
            select_logistics_carrier(order("客户指定物流"), resolved, configured),
            "中通快递（重货）",
        )

    def test_seller_remark_order_and_exact_deduplication(self) -> None:
        self.assertEqual(
            compose_seller_remark("周末送达", ["白标商品", "白标商品"]),
            "周末送达；白标商品",
        )
        self.assertEqual(
            compose_seller_remark("周末送达", ["白标商品"], separator="/"),
            "周末送达/白标商品",
        )


if __name__ == "__main__":
    unittest.main()
