from __future__ import annotations

import unittest

from auto_shipped.domain import ClarificationRequest, DecisionItem
from auto_shipped.services.decision_orchestrator import converge_decisions


class DecisionOrchestratorTests(unittest.TestCase):
    def test_blocker_wins_over_preview_decision(self) -> None:
        state = converge_decisions(
            clarifications=[
                ClarificationRequest(
                    code="MISSING_FACT",
                    question="请补充关键事实。",
                    reason="无法形成唯一结果。",
                )
            ],
            decisions=[
                DecisionItem(
                    code="PROVISIONAL",
                    source_ref="商品:测试",
                    field="商品",
                    proposed_value={"product_code": "P1"},
                    reason="唯一候选",
                    confirmation_question="仅本次还是以后沿用？",
                )
            ],
        )
        self.assertEqual(state.status, "needs_input")
        self.assertFalse(state.may_create_upload_manifest)

    def test_unique_provisional_decision_is_preview_only(self) -> None:
        state = converge_decisions(
            clarifications=[],
            decisions=[
                DecisionItem(
                    code="PROVISIONAL",
                    source_ref="商品:测试",
                    field="商品",
                    proposed_value={"product_code": "P1"},
                    reason="唯一候选",
                    confirmation_question="仅本次还是以后沿用？",
                )
            ],
        )
        self.assertEqual(state.status, "preview_ready")
        self.assertFalse(state.may_create_upload_manifest)

    def test_no_open_decision_is_ready(self) -> None:
        state = converge_decisions(clarifications=[], decisions=[])
        self.assertEqual(state.status, "ready")
        self.assertTrue(state.may_create_upload_manifest)


if __name__ == "__main__":
    unittest.main()
