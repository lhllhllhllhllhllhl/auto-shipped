from __future__ import annotations

import unittest

from auto_shipped.integrations.feishu import (
    CorrectionFeedbackRecord,
    FeishuIdentity,
    build_correction_feedback_record,
    collect_correction_feedback,
    submit_correction_feedback,
)


NOW = "2026-10-12T05:00:00+00:00"


class FakeFeedbackGateway:
    def __init__(self) -> None:
        self.identity = FeishuIdentity("ou-user", display_name="员工")
        self.records: list[CorrectionFeedbackRecord] = []
        self.drop_write = False

    def get_current_identity(self) -> FeishuIdentity:
        return self.identity

    def list_correction_feedback(self) -> list[CorrectionFeedbackRecord]:
        return list(self.records)

    def append_correction_feedback(self, record: CorrectionFeedbackRecord) -> None:
        if not self.drop_write:
            self.records.append(record)


def feedback(operation_id: str = "feedback-1") -> CorrectionFeedbackRecord:
    return build_correction_feedback_record(
        feedback_type="mapping_corrected",
        company_id="sam",
        source_profile_id="sam_order_v1",
        source_field="商品编码+规格编码",
        source_value="84292384",
        source_spec="SWSH8096",
        system_result="仅按商品编码映射",
        corrected_result="按商品编码和规格编码精确映射",
        reuse_scope="long_term",
        related_proposal_id="",
        evidence="用户纠错确认",
        skill_version="1.0.0",
        submitted_by="ou-user",
        submission_operation_id=operation_id,
        submitted_at=NOW,
    )


class CorrectionFeedbackTests(unittest.TestCase):
    def test_build_is_deterministic_for_same_operation(self) -> None:
        self.assertEqual(feedback(), feedback())

    def test_submit_is_append_only_idempotent_and_read_back(self) -> None:
        gateway = FakeFeedbackGateway()
        item = feedback()
        first = submit_correction_feedback(gateway, item)
        second = submit_correction_feedback(gateway, item)
        self.assertEqual(first.status, "submitted")
        self.assertEqual(second.status, "already_submitted")
        self.assertEqual(gateway.records, [item])

    def test_missing_readback_fails_closed(self) -> None:
        gateway = FakeFeedbackGateway()
        gateway.drop_write = True
        result = submit_correction_feedback(gateway, feedback())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, "FEEDBACK_WRITE_NOT_VERIFIED")

    def test_collect_groups_same_issue_without_promoting_it(self) -> None:
        first = feedback("feedback-1")
        second = feedback("feedback-2")
        summary = collect_correction_feedback([first, second])
        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["issue_count"], 1)
        self.assertEqual(summary["issues"][0]["count"], 2)


if __name__ == "__main__":
    unittest.main()
