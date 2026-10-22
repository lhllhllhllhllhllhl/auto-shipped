from __future__ import annotations

import json
import tempfile
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
        self.assertEqual(result["module_count"], 14)
        self.assertEqual(result["implementation_ref_count"], 30)
        self.assertEqual(result["legacy_implementation_ref_count"], 1)
        self.assertEqual(result["planned_implementation_ref_count"], 4)
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

    def test_catalog_requires_shared_authority_paths_to_be_declared(self) -> None:
        catalog_path = PROJECT_ROOT / "config/governance/system_module_catalog_v1.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["shared_authority_paths"] = []
        with tempfile.TemporaryDirectory() as directory:
            temporary_catalog = Path(directory) / "catalog.json"
            temporary_catalog.write_text(
                json.dumps(catalog, ensure_ascii=False),
                encoding="utf-8",
            )
            result = audit_module_catalog(
                temporary_catalog,
                project_root=PROJECT_ROOT,
                implementation_registry_path=PROJECT_ROOT
                / "config/business_rules/implementation_registry_v1.json",
                policy_registry_path=PROJECT_ROOT
                / "config/platform_rules/guanyi/policy_modules_v1.json",
            )
        issue_codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("AUTHORITY_PATH_SHARED_WITHOUT_DECLARATION", issue_codes)

    def test_generated_governance_doc_matches_machine_catalog(self) -> None:
        catalog = load_module_catalog(
            PROJECT_ROOT / "config/governance/system_module_catalog_v1.json"
        )
        from auto_shipped.rules.module_catalog import render_module_catalog_markdown

        self.assertEqual(
            (PROJECT_ROOT / "docs/MODULE_GOVERNANCE.md").read_text(encoding="utf-8"),
            render_module_catalog_markdown(catalog),
        )

    def test_catalog_rejects_unregistered_allowed_implementation(self) -> None:
        rule_catalog = json.loads(
            (
                PROJECT_ROOT
                / "config/business_rules/shangyu_sop_rule_catalog_v1.json"
            ).read_text(encoding="utf-8")
        )
        rule_catalog["rules"]["GY-PLATFORM-ORDER-NUMBER"][
            "allowed_implementation_refs"
        ].append("guanyi.platform_order.unregistered")
        with tempfile.TemporaryDirectory() as directory:
            temporary_rule_catalog = Path(directory) / "rules.json"
            temporary_rule_catalog.write_text(
                json.dumps(rule_catalog, ensure_ascii=False),
                encoding="utf-8",
            )
            result = audit_module_catalog(
                PROJECT_ROOT / "config/governance/system_module_catalog_v1.json",
                project_root=PROJECT_ROOT,
                implementation_registry_path=PROJECT_ROOT
                / "config/business_rules/implementation_registry_v1.json",
                policy_registry_path=PROJECT_ROOT
                / "config/platform_rules/guanyi/policy_modules_v1.json",
                rule_catalog_path=temporary_rule_catalog,
            )
        issue_codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("RULE_ALLOWED_IMPLEMENTATION_UNREGISTERED", issue_codes)


if __name__ == "__main__":
    unittest.main()
