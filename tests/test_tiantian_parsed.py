from __future__ import annotations

import unittest
from pathlib import Path

from auto_shipped.source_adapters import TiantianWarehouseParsedAdapter


PROFILE = {
    "profile_id": "tiantian_warehouse_v2",
    "source_label": "恬田代发仓",
    "order_type": "warehouse_order",
}


def sample_row() -> dict:
    return {
        "订单编号": "W1",
        "来源单号": "SRC-1",
        "下单时间": "2026-09-20 14:09:15",
        "收件人": "测试用户",
        "联系方式": "13800138000",
        "省": "上海",
        "市": "上海市",
        "区": "宝山区",
        "省市区详细地址": "上海市宝山区测试路1号",
        "商品编码": "EXT-1",
        "商品名称": "测试商品",
        "数量": 1,
        "客户备注": "",
        "物流公司": "",
        "物流单号": "",
        "渠道名称": "测试渠道",
    }


class TiantianParsedAdapterTests(unittest.TestCase):
    def test_rows_become_platform_neutral_parsed_order(self) -> None:
        first = sample_row()
        second = sample_row()
        second["订单编号"] = "W2"
        second["数量"] = 2
        result = TiantianWarehouseParsedAdapter().parse_rows(
            [(2, first), (3, second)],
            Path("sample.xlsx"),
            PROFILE,
        )
        self.assertEqual(result.status, "parsed")
        self.assertEqual(len(result.orders), 1)
        order = result.orders[0]
        self.assertEqual(order.source.source_order_no, "SRC-1")
        self.assertEqual(order.recipient.full_address, "上海市宝山区测试路1号")
        self.assertEqual([item.quantity for item in order.items], [1, 2])
        self.assertNotIn("platform_order_no", order.to_dict())
        self.assertNotIn("store_name", order.to_dict())

    def test_missing_recipient_address_becomes_clarification(self) -> None:
        row = sample_row()
        row["省市区详细地址"] = ""
        result = TiantianWarehouseParsedAdapter().parse_rows(
            [(2, row)],
            Path("sample.xlsx"),
            PROFILE,
        )
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.orders, [])
        self.assertIn(
            "MISSING_RECIPIENT_FIELD",
            {request.code for request in result.clarifications},
        )

    def test_invalid_contact_becomes_clarification(self) -> None:
        row = sample_row()
        row["联系方式"] = "abc"
        result = TiantianWarehouseParsedAdapter().parse_rows(
            [(2, row)],
            Path("sample.xlsx"),
            PROFILE,
        )
        self.assertEqual(result.status, "needs_input")
        self.assertIn(
            "INVALID_RECIPIENT_CONTACT",
            {request.code for request in result.clarifications},
        )

    def test_long_numeric_source_order_requires_text(self) -> None:
        row = sample_row()
        row["来源单号"] = 220722179066487320925437952
        result = TiantianWarehouseParsedAdapter().parse_rows(
            [(2, row)],
            Path("sample.xlsx"),
            PROFILE,
        )
        self.assertEqual(result.status, "needs_input")
        self.assertIn(
            "SOURCE_ORDER_NO_TEXT_REQUIRED",
            {request.code for request in result.clarifications},
        )


if __name__ == "__main__":
    unittest.main()
