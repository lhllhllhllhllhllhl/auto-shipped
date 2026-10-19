from __future__ import annotations

import unittest
from pathlib import Path

from auto_shipped.rules.module_catalog import audit_module_catalog, load_module_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ModuleCatalogTests(unittest.TestCase):
    def test_catalog_has_unique_complete_implementation_and_policy_ownership(self) -> None:
        result = audit_module_catalog(
            PROJECT_ROOT / "config/governance/system_module_catalog_v1.json",
            project_root=PROJECT_ROOT,
            implementation_registry_path=PROJECT_ROOT / "config/business_rules/implementation_registry_v1.json",
            policy_registry_path=PROJECT_ROOT / "config/platform_rules/guanyi/policy_modules_v1.json",
        )
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(result["module_count"], 13)
        self.assertEqual(result["implementation_ref_count"], 29)
        self.assertEqual(result["legacy_implementation_ref_count"], 1)
        self.assertEqual(result["policy_module_count"], 11)

    def test_catalog_keeps_browser_and_conversion_boundaries_separate(self) -> None:
        catalog = load_module_catalog(
            PROJECT_ROOT / "config/governance/system_module_catalog_v1.json"
        )
        modules = {item["module_id"]: item for item in catalog["modules"]}
        browser_refs = modules["browser_upload"]["implementation_refs"]
        self.assertTrue(all(ref.startswith("browser.guanyi.") for ref in browser_refs))
        self.assertFalse(
            any(
                ref.startswith("browser.guanyi.")
                for ref in modules["conversion_delivery"]["implementation_refs"]
            )
        )


if __name__ == "__main__":
    unittest.main()
