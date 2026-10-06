from __future__ import annotations

import unittest

from auto_shipped.domain import CanonicalOrder, OrderItem, Recipient, SourceRef
from auto_shipped.services.redaction import redact_order


class RedactionTests(unittest.TestCase):
    def test_default_preview_masks_recipient_pii(self) -> None:
        order = CanonicalOrder(
            order_id="o1",
            source=SourceRef("test", "sample.xlsx", "S1", "Sheet1!A2:P2"),
            platform_order_no="S1",
            buyer_member="张",
            store_name="光明满元气",
            warehouse_name="上海尚舆商贸有限公司",
            recipient=Recipient("张三", "13800138000", "上海", "上海市", "宝山区", "测试路1号"),
            items=[OrderItem("S1", "测试商品", 1, mapping_status="confirmed")],
        )
        data = redact_order(order)
        self.assertEqual(data["recipient"]["name"], "张*")
        self.assertEqual(data["recipient"]["mobile"], "138****8000")
        self.assertTrue(data["recipient"]["detail"].startswith("<已脱敏:"))


if __name__ == "__main__":
    unittest.main()

