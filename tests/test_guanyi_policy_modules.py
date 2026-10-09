from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_shipped.platforms.guanyi import (
    GuanyiPolicyModuleError,
    resolve_guanyi_policy_modules,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = PROJECT_ROOT / "config/platform_rules"


def load_rules(name: str) -> dict:
    raw = json.loads(
        (PROJECT_ROOT / "config/platform_rules/guanyi" / name).read_text(
            encoding="utf-8"
        )
    )
    return resolve_guanyi_policy_modules(raw, RULES_DIR)


class GuanyiPolicyModuleTests(unittest.TestCase):
    def test_corn_store_policy_has_one_executable_authority(self) -> None:
        policy_refs = []
        for name in (
            "tiantian_warehouse_v2.json",
            "ndd_order_v1.json",
            "rongzhida_text_v1.json",
        ):
            raw = json.loads(
                (PROJECT_ROOT / "config/platform_rules/guanyi" / name).read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("store_assignment_policy", raw)
            policy_refs.append(
                raw["policy_module_refs"]["store_assignment_policy"]["module_id"]
            )
            resolved = resolve_guanyi_policy_modules(raw, RULES_DIR)
            self.assertEqual(
                resolved["store_assignment_policy"]["rules"][0]["store"],
                "光明满元气",
            )
        self.assertEqual(set(policy_refs), {"store.corn_to_guangming_manyuanqi.v1"})

    def test_common_policies_are_materialized_for_source_rule_pack(self) -> None:
        rules = load_rules("tiantian_warehouse_v2.json")
        self.assertEqual(rules["buyer_member_policy"]["value"], "张")
        self.assertEqual(
            rules["defaults"]["warehouse_name"]["value"],
            "上海尚舆商贸有限公司",
        )
        self.assertEqual(rules["logistics_policy"]["default_carrier"], "韵达快递")
        self.assertEqual(
            rules["note_routing_policy"]["strategy"],
            "courier_instructions_to_address_other_notes_to_seller",
        )
        self.assertEqual(rules["seller_remark_policy"]["target_field"], "卖家备注")

    def test_all_implemented_rule_packs_share_one_note_routing_module(self) -> None:
        module_ids = set()
        for name in (
            "tiantian_warehouse_v2.json",
            "sam_order_v1.json",
            "ndd_order_v1.json",
            "self_operated_text_v1.json",
            "rongzhida_text_v1.json",
            "taojuzi_taobao_text_v1.json",
            "taojuzi_kqyd_text_v1.json",
        ):
            raw = json.loads(
                (PROJECT_ROOT / "config/platform_rules/guanyi" / name).read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("note_routing_policy", raw)
            module_ids.add(
                raw["policy_module_refs"]["note_routing_policy"]["module_id"]
            )
            self.assertIn(
                "GY-DELIVERY-INSTRUCTION-ADDRESS",
                raw["business_rule_coverage"]["rules"],
            )
        self.assertEqual(module_ids, {"note.courier_instruction_to_address.v1"})

    def test_source_override_is_explicit_and_does_not_copy_module(self) -> None:
        rules = load_rules("sam_order_v1.json")
        self.assertEqual(rules["defaults"]["store"]["value"], "sam")
        self.assertEqual(rules["defaults"]["freight"]["value"], "")

    def test_self_operated_jd_carrier_alias_is_source_scoped(self) -> None:
        self_operated = load_rules("self_operated_text_v1.json")
        common = load_rules("tiantian_warehouse_v2.json")
        self.assertEqual(
            self_operated["logistics_policy"]["source_carrier_aliases"]["京东中通"],
            "京东中通",
        )
        self.assertNotIn(
            "京东中通",
            common["logistics_policy"]["source_carrier_aliases"],
        )

    def test_taojuzi_text_rule_packs_share_store_and_default_yunda_modules(self) -> None:
        for name in (
            "taojuzi_taobao_text_v1.json",
            "taojuzi_kqyd_text_v1.json",
        ):
            raw = json.loads(
                (PROJECT_ROOT / "config/platform_rules/guanyi" / name).read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("store_assignment_policy", raw)
            self.assertNotIn("logistics_policy", raw)
            self.assertEqual(
                raw["policy_module_refs"]["store_assignment_policy"]["module_id"],
                "store.taojuzi_manual_channels.v1",
            )
            self.assertEqual(
                raw["policy_module_refs"]["logistics_policy"]["module_id"],
                "logistics.source_override_then_default_yunda.v1",
            )
            resolved = resolve_guanyi_policy_modules(raw, RULES_DIR)
            self.assertEqual(resolved["logistics_policy"]["default_carrier"], "韵达快递")

    def test_inline_and_referenced_policy_fails_closed(self) -> None:
        raw = json.loads(
            (PROJECT_ROOT / "config/platform_rules/guanyi/tiantian_warehouse_v2.json").read_text(
                encoding="utf-8"
            )
        )
        raw["store_assignment_policy"] = {"strategy": "unsafe_duplicate"}
        with self.assertRaises(GuanyiPolicyModuleError):
            resolve_guanyi_policy_modules(raw, RULES_DIR)


if __name__ == "__main__":
    unittest.main()
