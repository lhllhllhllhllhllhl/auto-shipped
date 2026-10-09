from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from auto_shipped.rules import RulePackSchemaError, validate_rule_pack


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def production_pack() -> dict:
    return json.loads(
        (
            PROJECT_ROOT
            / "config/platform_rules/guanyi/tiantian_warehouse_v2.json"
        ).read_text(encoding="utf-8")
    )


class RulePackSchemaTests(unittest.TestCase):
    def test_all_current_guanyi_rule_packs_match_core_schema(self) -> None:
        for path in (PROJECT_ROOT / "config/platform_rules/guanyi").glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if "profile_id" not in payload:
                continue
            validate_rule_pack(payload)

    def test_business_specific_extension_remains_allowed(self) -> None:
        payload = production_pack()
        payload["future_company_policy"] = {
            "strategy": "new_configurable_strategy",
            "company_specific_value": 123,
        }
        validate_rule_pack(payload)

    def test_missing_core_identity_fails_closed(self) -> None:
        payload = production_pack()
        del payload["source_profile_id"]
        with self.assertRaises(RulePackSchemaError):
            validate_rule_pack(payload)

    def test_implemented_rule_requires_string_reference(self) -> None:
        payload = copy.deepcopy(production_pack())
        declaration = next(
            iter(payload["business_rule_coverage"]["rules"].values())
        )
        declaration["implementation_ref"] = None
        with self.assertRaises(RulePackSchemaError):
            validate_rule_pack(payload)


if __name__ == "__main__":
    unittest.main()
