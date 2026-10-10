from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_shipped.companies import (
    audit_company_registry,
    load_company_registry,
    resolve_company_abbreviation_references,
    resolve_company_by_identity,
    resolve_company_by_source_profile,
    validate_company_workflow,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CompanyRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_company_registry(
            PROJECT_ROOT / "config/companies/company_registry_v1.json"
        )

    def test_tiantian_source_resolves_to_stable_company_id(self) -> None:
        result = resolve_company_by_source_profile(
            self.registry,
            "tiantian_warehouse_v2",
        )
        self.assertEqual(result.status, "resolved")
        self.assertIsNotNone(result.company)
        self.assertEqual(result.company.company_id, "tiantian")
        self.assertEqual(result.company.display_name, "恬田")

    def test_unknown_source_does_not_guess_a_company(self) -> None:
        result = resolve_company_by_source_profile(self.registry, "unknown_source")
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.code, "COMPANY_NOT_REGISTERED")

    def test_legal_name_and_abbreviation_resolve_registered_company(self) -> None:
        for value in ("上海恬田食品有限公司", "TT", "tt"):
            result = resolve_company_by_identity(self.registry, value)
            self.assertEqual(result.status, "resolved")
            self.assertIsNotNone(result.company)
            self.assertEqual(result.company.company_id, "tiantian")
            self.assertEqual(result.company.abbreviation, "TT")

    def test_identity_only_company_does_not_enable_conversion(self) -> None:
        for value in ("上海孚泽经贸有限公司", "FZ"):
            result = resolve_company_by_identity(self.registry, value)
            self.assertEqual(result.status, "needs_input")
            self.assertIsNotNone(result.company)
            self.assertEqual(result.company.company_id, "fuze")
            self.assertEqual(result.code, "COMPANY_WORKFLOW_NOT_REGISTERED")

    def test_company_abbreviation_resolves_only_explicit_rule_references(self) -> None:
        result = resolve_company_by_identity(self.registry, "NDD")
        self.assertEqual(result.status, "resolved")
        rules = {
            "platform_order_number": {
                "strategy": "source_order_no_with_affixes",
                "prefix_source": "company_abbreviation",
                "suffix": "",
            }
        }
        resolved = resolve_company_abbreviation_references(rules, result.company)
        self.assertEqual(resolved["platform_order_number"]["prefix"], "NDD")
        self.assertNotIn("prefix", rules["platform_order_number"])

    def test_tiantian_workflow_matches_route_references(self) -> None:
        routing = json.loads(
            (PROJECT_ROOT / "config/routing/order_routes_v1.json").read_text(
                encoding="utf-8"
            )
        )
        route = next(
            item
            for item in routing["routes"]
            if item["route_id"] == "tiantian_warehouse_to_guanyi_v2"
        )
        result = validate_company_workflow(
            self.registry,
            "tiantian",
            source_profile_id=route["source_profile_id"],
            route_id=route["route_id"],
            target_platform=route["target_platform"],
            target_profile_id=route["target_profile_id"],
            platform_rules_profile_id=route["platform_rules_profile_id"],
        )
        self.assertEqual(result.status, "resolved")

    def test_repository_company_references_are_complete(self) -> None:
        result = audit_company_registry(
            self.registry,
            source_profiles_dir=PROJECT_ROOT / "config/source_profiles",
            routing_path=PROJECT_ROOT / "config/routing/order_routes_v1.json",
            platform_profiles_dir=PROJECT_ROOT / "config/platform_profiles",
            platform_rules_dir=PROJECT_ROOT / "config/platform_rules",
            mappings_path=PROJECT_ROOT / "config/catalog/external_sku_mappings.json",
        )
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(result["company_count"], 8)
        self.assertEqual(result["workflow_count"], 7)

    def test_taojuzi_text_profiles_belong_to_self_operated_company(self) -> None:
        for profile_id in (
            "taojuzi_taobao_text_v1",
            "taojuzi_kqyd_text_v1",
        ):
            result = resolve_company_by_source_profile(self.registry, profile_id)
            self.assertEqual(result.status, "resolved")
            self.assertIsNotNone(result.company)
            self.assertEqual(result.company.company_id, "self_operated")


if __name__ == "__main__":
    unittest.main()
