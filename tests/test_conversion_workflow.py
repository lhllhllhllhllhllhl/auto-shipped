from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from auto_shipped.services.conversion_workflow import (
    ConversionWorkflowError,
    build_conversion_workflow,
    supported_conversion_rule_ids,
    verify_rendered_output,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _orders() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(items=[SimpleNamespace(), SimpleNamespace()]),
        SimpleNamespace(items=[SimpleNamespace()]),
    ]


class ConversionWorkflowTests(unittest.TestCase):
    def test_every_catalog_conversion_rule_has_runtime_verifier(self) -> None:
        catalog = json.loads(
            (
                PROJECT_ROOT
                / "config/business_rules/shangyu_sop_rule_catalog_v1.json"
            ).read_text(encoding="utf-8")
        )
        conversion_rules = {
            rule_id
            for rule_id, definition in catalog["rules"].items()
            if definition["phase"] == "conversion"
        }

        self.assertEqual(supported_conversion_rule_ids(), conversion_rules)

    def test_missing_rule_scope_blocks_delivery(self) -> None:
        events = [
            {
                "rule_id": "GY-LOGISTICS-SELECTION",
                "scope_key": "order:1",
                "status": "completed",
                "outcome": "selected",
            }
        ]
        lines = [
            {"平台单号": "A", "物流公司": "韵达快递", "数量": 1},
            {"平台单号": "B", "物流公司": "中通快递（重货）", "数量": 1},
        ]

        with self.assertRaises(ConversionWorkflowError) as raised:
            build_conversion_workflow(
                orders=_orders(),
                required_rule_ids=["GY-LOGISTICS-SELECTION"],
                rule_events=events,
                lines=lines,
                source_profile_id="test",
                company_id="test",
                route_id="test",
            )

        self.assertEqual(
            raised.exception.code,
            "CONVERSION_WORKFLOW_RULE_EXECUTION_INCOMPLETE",
        )
        self.assertEqual(
            raised.exception.details["missing_scope_keys"],
            ["order:2"],
        )

    def test_new_rule_without_runtime_check_blocks_delivery(self) -> None:
        with self.assertRaises(ConversionWorkflowError) as raised:
            build_conversion_workflow(
                orders=_orders(),
                required_rule_ids=["GY-UNREGISTERED-NEW-RULE"],
                rule_events=[],
                lines=[{"平台单号": "A", "数量": 1}],
                source_profile_id="test",
                company_id="test",
                route_id="test",
            )

        self.assertEqual(
            raised.exception.code,
            "CONVERSION_WORKFLOW_RULE_CHECK_MISSING",
        )

    def test_rendered_business_value_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Sheet1"
            sheet.append(["平台单号", "物流公司"])
            sheet.append(["NDD001", "韵达快递"])
            workbook.save(output)

            with self.assertRaises(ConversionWorkflowError) as raised:
                verify_rendered_output(
                    output,
                    expected_lines=[
                        {"平台单号": "NDD001", "物流公司": "中通快递（重货）"}
                    ],
                    platform_profile={
                        "sheet_name": "Sheet1",
                        "header_row": 1,
                        "columns": ["平台单号", "物流公司"],
                    },
                )

        self.assertEqual(
            raised.exception.code,
            "CONVERSION_WORKFLOW_RENDERED_VALUE_MISMATCH",
        )
        self.assertEqual(
            raised.exception.details["different_fields"],
            ["物流公司"],
        )


if __name__ == "__main__":
    unittest.main()
