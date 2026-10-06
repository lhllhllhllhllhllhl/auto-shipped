from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_shipped.companies import (
    audit_company_registry,
    load_company_registry,
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
        self.assertEqual(result["company_count"], 3)
        self.assertEqual(result["workflow_count"], 3)


if __name__ == "__main__":
    unittest.main()
