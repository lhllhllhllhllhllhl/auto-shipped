from __future__ import annotations

import unittest

from auto_shipped.platforms.guanyi.order_policies import ResolvedOrderItem
from auto_shipped.platforms.guanyi.store_assignment import (
    GuanyiStoreAssignmentError,
    resolve_store_assignment,
    select_store,
)


def item(code: str, spec: str = "S1") -> ResolvedOrderItem:
    return ResolvedOrderItem(1, code, spec, "测试商品", "测试规格")


def named_item(code: str, product_name: str, spec: str = "S1") -> ResolvedOrderItem:
    return ResolvedOrderItem(1, code, spec, product_name, "测试规格")


class GuanyiStoreAssignmentTests(unittest.TestCase):
    def test_confirmed_fixed_store_assignment(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "fixed",
                "store": "sam",
                "confirmed": True,
            }
        }
        self.assertEqual(select_store([item("P1")], rules), "sam")

    def test_unconfirmed_fixed_store_fails_closed(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "fixed",
                "store": "sam",
                "confirmed": False,
            }
        }
        with self.assertRaises(GuanyiStoreAssignmentError):
            select_store([item("P1")], rules)

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

    def test_source_channel_assigns_confirmed_store(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "source_extension_exact_map",
                "source_extension_field": "source_channel",
                "rules": [
                    {
                        "source_values": ["天猫"],
                        "store": "MANYANGCHI旗舰店",
                    },
                    {
                        "source_values": ["京东"],
                        "store": "MAN YUAN QI旗舰店",
                    },
                ],
            }
        }
        self.assertEqual(
            select_store(
                [item("P1")],
                rules,
                source_extensions={"source_channel": "天猫"},
            ),
            "MANYANGCHI旗舰店",
        )
        self.assertEqual(
            select_store(
                [item("P1")],
                rules,
                source_extensions={"source_channel": "京东"},
            ),
            "MAN YUAN QI旗舰店",
        )

    def test_unknown_source_channel_fails_closed(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "source_extension_exact_map",
                "source_extension_field": "source_channel",
                "rules": [
                    {"source_values": ["天猫"], "store": "天猫店"},
                ],
            }
        }
        with self.assertRaises(GuanyiStoreAssignmentError):
            select_store(
                [item("P1")],
                rules,
                source_extensions={"source_channel": "未知平台"},
            )

    def test_manual_store_route_prefers_exact_source_over_shared_fallback(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "manual_store_route_table",
                "confirmed": True,
                "fallback_scope_ids": ["manual_all"],
                "allowed_stores": ["光明满元气", "淘橘子"],
            }
        }
        routes = [
            {
                "scope_id": "manual_all",
                "product_code": "P1",
                "spec_code": "S1",
                "store": "淘橘子",
                "status": "active",
            },
            {
                "scope_id": "jzdz_manual_v1",
                "product_code": "P1",
                "spec_code": "S1",
                "store": "光明满元气",
                "status": "active",
            },
        ]

        self.assertEqual(
            select_store(
                [item("P1")],
                rules,
                source_profile_id="jzdz_manual_v1",
                manual_store_routes=routes,
            ),
            "光明满元气",
        )
        self.assertEqual(
            select_store(
                [item("P1")],
                rules,
                source_profile_id="drjy_manual_v1",
                manual_store_routes=routes,
            ),
            "淘橘子",
        )

    def test_manual_store_route_rejects_mixed_store_order(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "manual_store_route_table",
                "confirmed": True,
                "fallback_scope_ids": ["manual_all"],
                "allowed_stores": ["光明满元气", "淘橘子"],
            }
        }
        routes = [
            {
                "scope_id": "manual_all",
                "product_code": "P1",
                "spec_code": "S1",
                "store": "光明满元气",
                "status": "active",
            },
            {
                "scope_id": "manual_all",
                "product_code": "P2",
                "spec_code": "S1",
                "store": "淘橘子",
                "status": "active",
            },
        ]

        with self.assertRaises(GuanyiStoreAssignmentError):
            select_store(
                [item("P1"), item("P2")],
                rules,
                source_profile_id="jzdz_manual_v1",
                manual_store_routes=routes,
            )

    def test_generic_fallback_uses_confirmed_corn_rule_after_route_miss(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "manual_store_route_then_product",
                "confirmed": True,
                "include_company_scope": True,
                "include_source_profile_scope": False,
                "fallback_scope_ids": ["manual_all"],
                "allowed_stores": ["光明满元气", "淘橘子"],
                "product_fallback_rules": [
                    {
                        "store": "光明满元气",
                        "match": {"product_name_contains_all": ["玉米"]},
                    }
                ],
            }
        }
        self.assertEqual(
            select_store(
                [named_item("CORN9", "花糯玉米（9根装）")],
                rules,
                source_profile_id="generic_company_manual_v1",
            ),
            "光明满元气",
        )

    def test_generic_fallback_assigns_non_corn_to_taojuzi_with_notice(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "manual_store_route_then_product",
                "confirmed": True,
                "include_company_scope": True,
                "include_source_profile_scope": False,
                "fallback_scope_ids": ["manual_all"],
                "allowed_stores": ["光明满元气", "淘橘子"],
                "product_fallback_rules": [
                    {
                        "store": "光明满元气",
                        "match": {"product_name_contains_all": ["玉米"]},
                    },
                    {
                        "rule_id": "confirmed_non_corn_to_taojuzi",
                        "store": "淘橘子",
                        "match": {"product_name_excludes_any": ["玉米"]},
                        "result_notice": {
                            "code": "NON_CORN_TO_TAOJUZI_FALLBACK",
                            "message": "非玉米兜底",
                        },
                    }
                ],
            }
        }
        assignment = resolve_store_assignment(
            [named_item("TOWEL", "蓝色搓澡巾")],
            rules,
            source_profile_id="generic_company_manual_v1",
        )

        self.assertEqual(assignment.store, "淘橘子")
        self.assertEqual(len(assignment.notices), 1)
        self.assertEqual(
            assignment.notices[0].code,
            "NON_CORN_TO_TAOJUZI_FALLBACK",
        )

    def test_exact_store_route_overrides_non_corn_fallback_without_notice(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "manual_store_route_then_product",
                "confirmed": True,
                "include_company_scope": True,
                "include_source_profile_scope": False,
                "fallback_scope_ids": ["manual_all"],
                "allowed_stores": ["光明满元气", "淘橘子"],
                "product_fallback_rules": [
                    {
                        "rule_id": "confirmed_non_corn_to_taojuzi",
                        "store": "淘橘子",
                        "match": {"product_name_excludes_any": ["玉米"]},
                        "result_notice": {
                            "code": "NON_CORN_TO_TAOJUZI_FALLBACK",
                            "message": "非玉米兜底",
                        },
                    }
                ],
            }
        }
        assignment = resolve_store_assignment(
            [named_item("TOWEL", "蓝色搓澡巾")],
            rules,
            source_company_id="company-a",
            manual_store_routes=[
                {
                    "scope_id": "company-a",
                    "product_code": "TOWEL",
                    "spec_code": "S1",
                    "store": "光明满元气",
                    "status": "active",
                }
            ],
        )

        self.assertEqual(assignment.store, "光明满元气")
        self.assertEqual(assignment.notices, ())

    def test_corn_and_non_corn_fallbacks_still_reject_mixed_store_order(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "all_items_same_store_by_product",
                "rules": [
                    {
                        "rule_id": "corn",
                        "store": "光明满元气",
                        "match": {"product_name_contains_all": ["玉米"]},
                    },
                    {
                        "rule_id": "non-corn",
                        "store": "淘橘子",
                        "match": {"product_name_excludes_any": ["玉米"]},
                    },
                ],
            }
        }
        with self.assertRaises(GuanyiStoreAssignmentError):
            select_store(
                [
                    named_item("CORN", "花糯玉米"),
                    named_item("TOWEL", "蓝色搓澡巾"),
                ],
                rules,
            )

    def test_generic_fallback_company_store_routes_do_not_cross_companies(self) -> None:
        rules = {
            "store_assignment_policy": {
                "strategy": "manual_store_route_then_product",
                "confirmed": True,
                "include_company_scope": True,
                "include_source_profile_scope": False,
                "fallback_scope_ids": ["manual_all"],
                "allowed_stores": ["光明满元气", "淘橘子"],
            }
        }
        routes = [
            {
                "scope_id": "shanghai_shimao_plaza",
                "product_code": "P1",
                "spec_code": "S1",
                "store": "光明满元气",
                "status": "active",
            },
            {
                "scope_id": "fuze",
                "product_code": "P1",
                "spec_code": "S1",
                "store": "淘橘子",
                "status": "active",
            },
        ]

        self.assertEqual(
            select_store(
                [item("P1")],
                rules,
                source_company_id="shanghai_shimao_plaza",
                source_profile_id="generic_company_manual_v1",
                manual_store_routes=routes,
            ),
            "光明满元气",
        )
        self.assertEqual(
            select_store(
                [item("P1")],
                rules,
                source_company_id="fuze",
                source_profile_id="generic_company_manual_v1",
                manual_store_routes=routes,
            ),
            "淘橘子",
        )
