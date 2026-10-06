from __future__ import annotations

import unittest

from auto_shipped.platforms.guanyi.order_policies import ResolvedOrderItem
from auto_shipped.platforms.guanyi.store_assignment import (
    GuanyiStoreAssignmentError,
    select_store,
)


def item(code: str, spec: str = "S1") -> ResolvedOrderItem:
    return ResolvedOrderItem(1, code, spec, "测试商品", "测试规格")


class GuanyiStoreAssignmentTests(unittest.TestCase):
    def test_exact_product_and_spec_assign_one_store(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "all_items_same_store_by_product",
                "rules": [
                    {
                        "store": "光明满元气",
                        "match": {"product_codes": ["P1"], "spec_codes": ["S1"]},
                    }
                ],
            }
        }
        self.assertEqual(select_store([item("P1")], rules), "光明满元气")

    def test_unmatched_product_fails_closed(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "all_items_same_store_by_product",
                "rules": [
                    {"store": "光明满元气", "match": {"product_codes": ["P1"]}}
                ],
            }
        }
        with self.assertRaises(GuanyiStoreAssignmentError):
            select_store([item("P2")], rules)

    def test_order_spanning_multiple_stores_fails_closed(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "all_items_same_store_by_product",
                "rules": [
                    {"store": "店铺一", "match": {"product_codes": ["P1"]}},
                    {"store": "店铺二", "match": {"product_codes": ["P2"]}},
                ],
            }
        }
        with self.assertRaises(GuanyiStoreAssignmentError):
            select_store([item("P1"), item("P2")], rules)
