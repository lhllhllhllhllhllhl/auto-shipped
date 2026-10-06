from __future__ import annotations

import unittest

from auto_shipped.domain import CanonicalOrder, Issue, OrderItem, Recipient, SourceRef
from auto_shipped.exporters import BlockedOrderError, build_guanyi_records


def order_with(issues: list[Issue] | None = None) -> CanonicalOrder:
    return CanonicalOrder(
        order_id="o1",
        source=SourceRef(
            "test",
            "sample.xlsx",
            "SRC-1",
            "Sheet1!A2:P2",
            created_at="2026-09-20 14:09:15",
        ),
        platform_order_no="SRC-1",
        buyer_member="张",
        store_name="光明满元气",
        warehouse_name="上海尚舆商贸有限公司",
        recipient=Recipient("测试", "13800138000", "上海", "上海市", "宝山区", "测试路1号"),
        items=[
            OrderItem(
                external_sku="EXT",
                title="黄糯玉米8棒家庭装",
                quantity=1,
                product_code="JTW8E1",
                spec_code="6974768564811",
                mapping_status="confirmed",
            )
        ],
        issues=issues or [],
    )


class GuanyiRecordTests(unittest.TestCase):
    def test_ready_order_maps_to_main_and_detail_records(self) -> None:
        result = build_guanyi_records(order_with())
        self.assertEqual(result.main["平台单号"], "SRC-1")
        self.assertEqual(result.main["店铺名称（必填）"], "光明满元气")
        self.assertIn("上海市", result.main["收货地址（必填）"])
        self.assertEqual(result.details[0]["商品代码（必填）"], "JTW8E1")
        self.assertEqual(result.details[0]["规格名称"], "6974768564811")

    def test_blocked_order_cannot_reach_export_records(self) -> None:
        blocked = order_with([Issue("UNCONFIRMED_RULE", "待确认")])
        with self.assertRaises(BlockedOrderError):
            build_guanyi_records(blocked)


if __name__ == "__main__":
    unittest.main()

