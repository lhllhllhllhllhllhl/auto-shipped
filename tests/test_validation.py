from __future__ import annotations

import unittest

from auto_shipped.domain import CanonicalOrder, OrderItem, Recipient, SourceRef
from auto_shipped.validation import validate_order, validate_order_batch


def build_order(platform_no: str = "P1") -> CanonicalOrder:
    return CanonicalOrder(
        order_id="o1",
        source=SourceRef("test", "sample.xlsx", platform_no, "Sheet1!2:2"),
        platform_order_no=platform_no,
        buyer_member="张",
        store_name="光明满元气",
        warehouse_name="上海尚舆商贸有限公司",
        recipient=Recipient("测试", "13800138000", "上海", "上海市", "宝山区", "测试路1号"),
        items=[OrderItem("EXT", "测试商品", 1, product_code="P1", spec_code="S1", mapping_status="confirmed")],
    )


class ValidationTests(unittest.TestCase):
    def test_valid_order_has_no_validation_issue(self) -> None:
        self.assertEqual(validate_order(build_order()), [])

    def test_batch_detects_duplicate_ids(self) -> None:
        issues = validate_order_batch([build_order("P1"), build_order("P1")])
        self.assertEqual(
            {issue.code for issue in issues},
            {"DUPLICATE_PLATFORM_ORDER_NO", "DUPLICATE_SOURCE_ORDER_ID"},
        )


if __name__ == "__main__":
    unittest.main()

