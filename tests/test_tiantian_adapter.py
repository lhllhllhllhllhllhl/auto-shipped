from __future__ import annotations

import unittest

from auto_shipped.catalog.product_catalog import ProductCatalog, ProductRecord
from auto_shipped.source_adapters import TiantianWarehouseAdapter


PROFILE = {
    "profile_id": "tiantian_warehouse_v1",
    "company": "恬田代发仓",
    "defaults": {
        "buyer_member": "张",
        "store_name": "光明满元气",
        "warehouse_name": "上海尚舆商贸有限公司",
        "order_status": "买家已付款，等待卖家发货",
        "order_type": "销售订单",
        "payment_method": "网银在线",
        "payment_amount": 0,
        "freight": 0,
        "unit_price": 0,
    },
    "platform_order_number": {"strategy": "source_order_no", "confirmed": False},
    "logistics": {"strategy": "rules_engine"},
}


class TiantianAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = ProductRecord("JTW8E1", "黄糯玉米8棒家庭装", "6974768564811", "8袋/箱", 1210, "上海尚舆商贸有限公司")
        self.catalog = ProductCatalog(
            [self.record],
            {
                "2026DFYUMI001": {
                    "product_code": "JTW8E1",
                    "spec_code": "6974768564811",
                    "confirmed": False,
                    "reason": "待业务确认",
                }
            },
        )

    def sample_row(self) -> dict:
        return {
            "订单编号": "W1",
            "来源单号": "SRC-1",
            "下单时间": "2026-09-20 14:09:15",
            "收件人": "测试用户",
            "联系方式": "13800138000",
            "省": "上海",
            "市": "上海市",
            "区": "宝山区",
            "省市区详细地址": "测试路1号",
            "商品编码": "2026DFYUMI001",
            "商品名称": "黄糯玉米8棒家庭装",
            "数量": 1,
            "客户备注": "",
            "物流公司": "",
            "物流单号": "",
            "渠道名称": "测试渠道",
        }

    def test_row_becomes_blocked_canonical_order_until_rules_confirmed(self) -> None:
        orders = TiantianWarehouseAdapter().adapt_rows(
            [(2, self.sample_row())], "sample.xlsx", PROFILE, self.catalog
        )
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order.platform_order_no, "SRC-1")
        self.assertEqual(order.items[0].product_code, "JTW8E1")
        self.assertEqual(order.review_status, "blocked")
        self.assertEqual(
            {issue.code for issue in order.issues},
            {"UNCONFIRMED_PLATFORM_NUMBER_STRATEGY", "UNCONFIRMED_SKU_MAPPING"},
        )

    def test_rows_with_same_source_order_are_grouped_as_one_order(self) -> None:
        first = self.sample_row()
        second = self.sample_row()
        second["订单编号"] = "W2"
        second["数量"] = 2
        orders = TiantianWarehouseAdapter().adapt_rows(
            [(2, first), (3, second)], "sample.xlsx", PROFILE, self.catalog
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(orders[0].items), 2)
        self.assertEqual([item.quantity for item in orders[0].items], [1, 2])
        self.assertEqual(orders[0].source.row_ref, "仓库订单!A2:P3")


if __name__ == "__main__":
    unittest.main()
