from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_shipped.rules import load_implementation_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ImplementationRegistryTests(unittest.TestCase):
    def test_registered_targets_and_tests_exist(self) -> None:
        registry = load_implementation_registry(
            PROJECT_ROOT
            / "config/business_rules/implementation_registry_v1.json",
            project_root=PROJECT_ROOT,
            verify_test_files=True,
        )
        self.assertEqual(registry.registry_id, "shangyu_rule_implementations_v1")
        self.assertGreaterEqual(len(registry.implementations), 20)

    def test_every_current_implemented_declaration_is_registered(self) -> None:
        registry = load_implementation_registry(
            PROJECT_ROOT
            / "config/business_rules/implementation_registry_v1.json",
            project_root=PROJECT_ROOT,
        )
        for path in (PROJECT_ROOT / "config/platform_rules/guanyi").glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            coverage = payload.get("business_rule_coverage") or {}
            for rule_id, declaration in (coverage.get("rules") or {}).items():
                if declaration.get("status") != "implemented":
                    continue
                reference = declaration.get("implementation_ref")
                self.assertTrue(
                    registry.contains(reference),
                    f"{path.name}:{rule_id}未登记实现引用{reference}",
                )


if __name__ == "__main__":
    unittest.main()
