from __future__ import annotations

import json
import unittest
from pathlib import Path

from auto_shipped.rules import audit_rule_coverage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def minimal_catalog() -> dict:
    return {
        "catalog_id": "test_catalog",
        "rules": {
            "RULE-A": {
                "title": "规则A",
                "allowed_implementation_refs": ["test.a"],
            },
            "RULE-B": {
                "title": "规则B",
                "question": "请确认规则B",
            },
        },
        "scopes": {
            "test_scope": {
                "required_rules": {"conversion": ["RULE-A", "RULE-B"]}
            }
        },
    }


class BusinessRuleCoverageTests(unittest.TestCase):
    def test_ready_requires_confirmed_implementation_or_not_applicable(self) -> None:
        rules = {
            "business_rule_coverage": {
                "catalog_id": "test_catalog",
                "scope_id": "test_scope",
                "rules": {
                    "RULE-A": {
                        "status": "implemented",
                        "confirmed": True,
                        "implementation_ref": "test.a",
                    },
                    "RULE-B": {
                        "status": "not_applicable",
                        "confirmed": True,
                        "reason": "当前来源不适用",
                    },
                },
            }
        }
        result = audit_rule_coverage(
            minimal_catalog(), rules, "test_scope", phase="conversion"
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(set(result.ready_rule_ids), {"RULE-A", "RULE-B"})

    def test_pending_and_missing_rules_both_block(self) -> None:
        rules = {
            "business_rule_coverage": {
                "catalog_id": "test_catalog",
                "scope_id": "test_scope",
                "rules": {
                    "RULE-B": {
                        "status": "pending_confirmation",
                        "confirmed": False,
                    }
                },
            }
        }
        result = audit_rule_coverage(
            minimal_catalog(), rules, "test_scope", phase="conversion"
        )
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(set(result.blocked_rule_ids), {"RULE-A", "RULE-B"})
        codes = {item.code for item in result.clarifications}
        self.assertIn("BUSINESS_RULE_DECLARATION_MISSING_RULE_A", codes)
        self.assertIn("BUSINESS_RULE_PENDING_CONFIRMATION_RULE_B", codes)

    def test_invalid_implementation_reference_blocks(self) -> None:
        rules = {
            "business_rule_coverage": {
                "catalog_id": "test_catalog",
                "scope_id": "test_scope",
                "rules": {
                    "RULE-A": {
                        "status": "implemented",
                        "confirmed": True,
                        "implementation_ref": "unknown.impl",
                    },
                    "RULE-B": {
                        "status": "not_applicable",
                        "confirmed": True,
                    },
                },
            }
        }
        result = audit_rule_coverage(
            minimal_catalog(), rules, "test_scope", phase="conversion"
        )
        self.assertEqual(result.status, "needs_input")
        self.assertIn("RULE-A", result.blocked_rule_ids)
        self.assertTrue(
            any(
                item.code == "BUSINESS_RULE_IMPLEMENTATION_INVALID_RULE_A"
                for item in result.clarifications
            )
        )

    def test_production_tiantian_conversion_rules_are_ready(self) -> None:
        catalog = json.loads(
            (
                PROJECT_ROOT
                / "config/business_rules/shangyu_sop_rule_catalog_v1.json"
            ).read_text(encoding="utf-8")
        )
        rules = json.loads(
            (
                PROJECT_ROOT
                / "config/platform_rules/guanyi/tiantian_warehouse_v2.json"
            ).read_text(encoding="utf-8")
        )
        conversion = audit_rule_coverage(
            catalog, rules, "tiantian_to_guanyi_v1", phase="conversion"
        )
        self.assertEqual(conversion.status, "ready")
        self.assertEqual(conversion.blocked_rule_ids, [])

        upload = audit_rule_coverage(
            catalog, rules, "tiantian_to_guanyi_v1", phase="upload"
        )
        self.assertEqual(upload.status, "needs_input")
        self.assertEqual(
            set(upload.blocked_rule_ids),
            {"GY-IMPORT-OPTIONS", "GY-UPLOAD-SUBMISSION"},
        )


if __name__ == "__main__":
    unittest.main()
