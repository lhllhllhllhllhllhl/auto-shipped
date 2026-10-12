from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from .pending_mappings import FeishuIdentity


CORRECTION_FEEDBACK_SCHEMA_VERSION = "1.0"
CORRECTION_FEEDBACK_CONFIRMATION_TOKEN = "CONFIRM_SUBMIT_CORRECTION_FEEDBACK"
ALLOWED_FEEDBACK_TYPES = {
    "mapping_rejected",
    "mapping_corrected",
    "company_corrected",
    "field_corrected",
    "rule_disputed",
    "output_edited",
}
ALLOWED_REUSE_SCOPES = {"current_batch_only", "long_term"}
CORRECTION_FEEDBACK_HEADERS = (
    "feedback_id",
    "issue_key",
    "feedback_type",
    "company_id",
    "source_profile_id",
    "source_field",
    "source_value",
    "source_spec",
    "system_result",
    "corrected_result",
    "reuse_scope",
    "related_proposal_id",
    "evidence",
    "skill_version",
    "submitted_by",
    "submitted_at",
    "submission_operation_id",
    "status",
    "maintainer_note",
    "fixed_version",
)


@dataclass(frozen=True, slots=True)
class CorrectionFeedbackRecord:
    feedback_id: str
    issue_key: str
    feedback_type: str
    company_id: str
    source_profile_id: str
    source_field: str
    source_value: str
    source_spec: str
    system_result: str
    corrected_result: str
    reuse_scope: str
    related_proposal_id: str
    evidence: str
    skill_version: str
    submitted_by: str
    submitted_at: str
    submission_operation_id: str
    status: str = "new"
    maintainer_note: str = ""
    fixed_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CorrectionFeedbackSubmissionResult:
    status: str
    feedback_id: str
    code: str = ""
    reason: str = ""


class CorrectionFeedbackGateway(Protocol):
    def get_current_identity(self) -> FeishuIdentity: ...

    def list_correction_feedback(self) -> list[CorrectionFeedbackRecord]: ...

    def append_correction_feedback(self, record: CorrectionFeedbackRecord) -> None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name}不能为空")
    return cleaned


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", " ", text)


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(_normalized(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:24]}"


def build_correction_feedback_record(
    *,
    feedback_type: str,
    company_id: str,
    source_profile_id: str,
    source_field: str,
    source_value: str,
    source_spec: str,
    system_result: str,
    corrected_result: str,
    reuse_scope: str,
    related_proposal_id: str,
    evidence: str,
    skill_version: str,
    submitted_by: str,
    submission_operation_id: str | None = None,
    submitted_at: str | None = None,
) -> CorrectionFeedbackRecord:
    kind = _required(feedback_type, "feedback_type")
    if kind not in ALLOWED_FEEDBACK_TYPES:
        raise ValueError(f"未知feedback_type：{kind}")
    scope = _required(reuse_scope, "reuse_scope")
    if scope not in ALLOWED_REUSE_SCOPES:
        raise ValueError(f"未知reuse_scope：{scope}")
    user_id = _required(submitted_by, "submitted_by")
    operation_id = submission_operation_id or f"feedback-{uuid.uuid4()}"
    issue_key = _stable_id(
        "issue",
        kind,
        company_id,
        source_profile_id,
        source_field,
        source_value,
        source_spec,
        system_result,
    )
    feedback_id = _stable_id("feedback", user_id, operation_id, issue_key)
    return CorrectionFeedbackRecord(
        feedback_id=feedback_id,
        issue_key=issue_key,
        feedback_type=kind,
        company_id=_required(company_id, "company_id"),
        source_profile_id=_required(source_profile_id, "source_profile_id"),
        source_field=_required(source_field, "source_field"),
        source_value=_required(source_value, "source_value"),
        source_spec=str(source_spec or "").strip(),
        system_result=_required(system_result, "system_result"),
        corrected_result=_required(corrected_result, "corrected_result"),
        reuse_scope=scope,
        related_proposal_id=str(related_proposal_id or "").strip(),
        evidence=_required(evidence, "evidence"),
        skill_version=_required(skill_version, "skill_version"),
        submitted_by=user_id,
        submitted_at=submitted_at or _now(),
        submission_operation_id=_required(operation_id, "submission_operation_id"),
    )


def submit_correction_feedback(
    gateway: CorrectionFeedbackGateway,
    record: CorrectionFeedbackRecord,
) -> CorrectionFeedbackSubmissionResult:
    identity = gateway.get_current_identity()
    if identity.identity_type != "user" or not identity.user_id.strip():
        return CorrectionFeedbackSubmissionResult(
            status="needs_setup",
            feedback_id=record.feedback_id,
            code="FEISHU_USER_IDENTITY_REQUIRED",
            reason="纠错反馈必须使用当前员工的飞书用户身份。",
        )
    if identity.user_id != record.submitted_by:
        return CorrectionFeedbackSubmissionResult(
            status="failed",
            feedback_id=record.feedback_id,
            code="FEEDBACK_SUBMITTER_MISMATCH",
            reason="反馈提交者与当前飞书用户不一致。",
        )

    before = [
        item
        for item in gateway.list_correction_feedback()
        if item.feedback_id == record.feedback_id
    ]
    if before:
        if before == [record]:
            return CorrectionFeedbackSubmissionResult(
                status="already_submitted",
                feedback_id=record.feedback_id,
            )
        return CorrectionFeedbackSubmissionResult(
            status="failed",
            feedback_id=record.feedback_id,
            code="FEEDBACK_ID_CONFLICT",
            reason="同一feedback_id已存在不同内容。",
        )

    gateway.append_correction_feedback(record)
    readback = [
        item
        for item in gateway.list_correction_feedback()
        if item.feedback_id == record.feedback_id
    ]
    if readback != [record]:
        return CorrectionFeedbackSubmissionResult(
            status="failed",
            feedback_id=record.feedback_id,
            code="FEEDBACK_WRITE_NOT_VERIFIED",
            reason="飞书写入后无法精确回读该反馈。",
        )
    return CorrectionFeedbackSubmissionResult(
        status="submitted",
        feedback_id=record.feedback_id,
    )


def collect_correction_feedback(
    records: Sequence[CorrectionFeedbackRecord],
    *,
    status: str = "new",
) -> dict[str, Any]:
    selected = [record for record in records if not status or record.status == status]
    grouped: dict[str, list[CorrectionFeedbackRecord]] = {}
    for record in selected:
        grouped.setdefault(record.issue_key, []).append(record)
    issues = []
    for issue_key, items in sorted(grouped.items()):
        first = sorted(items, key=lambda item: (item.submitted_at, item.feedback_id))[0]
        issues.append(
            {
                "issue_key": issue_key,
                "feedback_type": first.feedback_type,
                "company_id": first.company_id,
                "source_profile_id": first.source_profile_id,
                "source_field": first.source_field,
                "source_value": first.source_value,
                "source_spec": first.source_spec,
                "system_result": first.system_result,
                "corrected_results": sorted(
                    {item.corrected_result for item in items}
                ),
                "reuse_scopes": sorted({item.reuse_scope for item in items}),
                "related_proposal_ids": sorted(
                    {item.related_proposal_id for item in items if item.related_proposal_id}
                ),
                "feedback_ids": sorted(item.feedback_id for item in items),
                "count": len(items),
            }
        )
    return {
        "status": "ready",
        "filter_status": status,
        "record_count": len(selected),
        "issue_count": len(issues),
        "issues": issues,
    }
