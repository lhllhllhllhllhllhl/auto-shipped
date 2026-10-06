from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol


PENDING_SHEET_SCHEMA_VERSION = "1.0"
PENDING_PROPOSAL_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class FeishuIdentity:
    user_id: str
    identity_type: str = "user"
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class UserSheetRoute:
    feishu_user_id: str
    sheet_id: str
    sheet_title: str
    status: str
    schema_version: str
    provision_key: str
    provision_operation_id: str
    created_at: str
    last_used_at: str
    conflict_sheet_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["conflict_sheet_ids"] = list(self.conflict_sheet_ids)
        return data


@dataclass(frozen=True, slots=True)
class PendingSheetMetadata:
    owner_user_id: str
    schema_version: str
    provision_key: str
    created_by_operation: str
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ManagedPendingSheet:
    sheet_id: str
    title: str
    metadata: PendingSheetMetadata | None = None


@dataclass(frozen=True, slots=True)
class PendingSheetResolution:
    status: str
    sheet_id: str | None = None
    sheet_title: str | None = None
    feishu_user_id: str | None = None
    created: bool = False
    repaired_route: bool = False
    code: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PendingMappingProposal:
    proposal_id: str
    mapping_key: str
    company_id: str
    source_profile_id: str
    identifier_type: str
    source_value: str
    source_spec: str
    target_platform: str
    candidate_product_code: str
    candidate_spec_code: str
    evidence: str
    base_official_revision: str
    submitted_by: str
    submitted_at: str
    submission_operation_id: str
    proposal_status: str = "pending"
    schema_version: str = PENDING_PROPOSAL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProposalSubmissionResult:
    status: str
    proposal_id: str
    sheet_id: str | None = None
    code: str | None = None
    reason: str | None = None


class FeishuSheetGateway(Protocol):
    """Provider-neutral operations supplied by an Agent connector or CLI adapter."""

    def get_current_identity(self) -> FeishuIdentity: ...

    def list_user_routes(self) -> list[UserSheetRoute]: ...

    def upsert_user_route(self, route: UserSheetRoute) -> None: ...

    def list_managed_pending_sheets(self) -> list[ManagedPendingSheet]: ...

    def create_pending_sheet_from_template(
        self,
        *,
        title: str,
        template_sheet_id: str,
        provision_operation_id: str,
    ) -> ManagedPendingSheet: ...

    def write_pending_sheet_metadata(
        self,
        sheet_id: str,
        metadata: PendingSheetMetadata,
    ) -> None: ...

    def list_pending_proposals(self, sheet_id: str) -> list[PendingMappingProposal]: ...

    def append_pending_proposal(
        self,
        sheet_id: str,
        proposal: PendingMappingProposal,
    ) -> None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", text).casefold()


def _required(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name}不能为空")
    return cleaned


def _provision_key(feishu_user_id: str) -> str:
    user_id = _required(feishu_user_id, "feishu_user_id")
    return f"feishu-user-{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:20]}"


def _sheet_title(identity: FeishuIdentity) -> str:
    label = identity.display_name.strip() or "用户"
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", label).strip("_")
    safe = safe[:24] or "用户"
    return f"待确认_{safe}_{_provision_key(identity.user_id)[-8:]}"


def build_mapping_key(
    *,
    company_id: str,
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
    target_platform: str,
) -> str:
    components = [
        _required(company_id, "company_id"),
        _required(source_profile_id, "source_profile_id"),
        _required(identifier_type, "identifier_type"),
        _required(source_value, "source_value"),
        str(source_spec or ""),
        _required(target_platform, "target_platform"),
    ]
    normalized = "\x1f".join(_normalized(value) for value in components)
    return f"mapping-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def build_pending_mapping_proposal(
    *,
    company_id: str,
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
    target_platform: str,
    candidate_product_code: str,
    candidate_spec_code: str,
    evidence: str,
    base_official_revision: str,
    submitted_by: str,
    submission_operation_id: str | None = None,
    submitted_at: str | None = None,
) -> PendingMappingProposal:
    operation_id = submission_operation_id or f"submit-{uuid.uuid4()}"
    mapping_key = build_mapping_key(
        company_id=company_id,
        source_profile_id=source_profile_id,
        identifier_type=identifier_type,
        source_value=source_value,
        source_spec=source_spec,
        target_platform=target_platform,
    )
    user_id = _required(submitted_by, "submitted_by")
    raw = "\x1f".join([user_id, operation_id, mapping_key]).encode("utf-8")
    proposal_id = f"proposal-{hashlib.sha256(raw).hexdigest()[:24]}"
    return PendingMappingProposal(
        proposal_id=proposal_id,
        mapping_key=mapping_key,
        company_id=_required(company_id, "company_id"),
        source_profile_id=_required(source_profile_id, "source_profile_id"),
        identifier_type=_required(identifier_type, "identifier_type"),
        source_value=_required(source_value, "source_value"),
        source_spec=str(source_spec or "").strip(),
        target_platform=_required(target_platform, "target_platform"),
        candidate_product_code=_required(
            candidate_product_code, "candidate_product_code"
        ),
        candidate_spec_code=_required(candidate_spec_code, "candidate_spec_code"),
        evidence=_required(evidence, "evidence"),
        base_official_revision=_required(
            base_official_revision, "base_official_revision"
        ),
        submitted_by=user_id,
        submitted_at=submitted_at or _now(),
        submission_operation_id=operation_id,
    )


def _valid_owned_sheets(
    sheets: list[ManagedPendingSheet],
    user_id: str,
) -> list[ManagedPendingSheet]:
    return [
        sheet
        for sheet in sheets
        if sheet.metadata is not None
        and sheet.metadata.owner_user_id == user_id
        and sheet.metadata.status == "active"
        and sheet.metadata.schema_version == PENDING_SHEET_SCHEMA_VERSION
    ]


def _conflict_resolution(
    gateway: FeishuSheetGateway,
    identity: FeishuIdentity,
    sheets: list[ManagedPendingSheet],
    *,
    provision_operation_id: str,
    now: str,
) -> PendingSheetResolution:
    sheet_ids = tuple(sorted(sheet.sheet_id for sheet in sheets))
    gateway.upsert_user_route(
        UserSheetRoute(
            feishu_user_id=identity.user_id,
            sheet_id="",
            sheet_title="",
            status="conflict",
            schema_version=PENDING_SHEET_SCHEMA_VERSION,
            provision_key=_provision_key(identity.user_id),
            provision_operation_id=provision_operation_id,
            created_at=now,
            last_used_at=now,
            conflict_sheet_ids=sheet_ids,
        )
    )
    return PendingSheetResolution(
        status="needs_input",
        feishu_user_id=identity.user_id,
        code="USER_PENDING_SHEET_CONFLICT",
        reason=f"同一飞书用户对应多个待确认Sheet：{', '.join(sheet_ids)}",
    )


def resolve_or_provision_user_sheet(
    gateway: FeishuSheetGateway,
    *,
    template_sheet_id: str,
    provision_operation_id: str | None = None,
    timestamp: str | None = None,
) -> PendingSheetResolution:
    identity = gateway.get_current_identity()
    if identity.identity_type != "user" or not identity.user_id.strip():
        return PendingSheetResolution(
            status="needs_setup",
            code="FEISHU_USER_IDENTITY_REQUIRED",
            reason="待确认映射必须使用当前员工的飞书用户身份，不能使用共享机器人身份。",
        )

    user_id = identity.user_id.strip()
    now = timestamp or _now()
    operation_id = provision_operation_id or f"provision-{uuid.uuid4()}"
    routes = [
        route
        for route in gateway.list_user_routes()
        if route.feishu_user_id == user_id
    ]
    if len(routes) > 1:
        return PendingSheetResolution(
            status="needs_input",
            feishu_user_id=user_id,
            code="USER_ROUTE_DUPLICATE",
            reason="用户路由表中存在多条相同飞书用户记录。",
        )
    if routes and routes[0].status == "disabled":
        return PendingSheetResolution(
            status="needs_input",
            feishu_user_id=user_id,
            code="USER_PENDING_SHEET_DISABLED",
            reason="当前飞书用户的待确认映射入口已停用。",
        )

    sheets = gateway.list_managed_pending_sheets()
    by_id = {sheet.sheet_id: sheet for sheet in sheets}
    if routes and routes[0].status == "active":
        route = routes[0]
        sheet = by_id.get(route.sheet_id)
        if (
            sheet is not None
            and sheet.metadata is not None
            and sheet.metadata.owner_user_id == user_id
            and sheet.metadata.schema_version == PENDING_SHEET_SCHEMA_VERSION
            and sheet.metadata.status == "active"
        ):
            gateway.upsert_user_route(replace(route, last_used_at=now))
            return PendingSheetResolution(
                status="ready",
                sheet_id=sheet.sheet_id,
                sheet_title=sheet.title,
                feishu_user_id=user_id,
            )

    owned = _valid_owned_sheets(sheets, user_id)
    if len(owned) > 1:
        return _conflict_resolution(
            gateway,
            identity,
            owned,
            provision_operation_id=operation_id,
            now=now,
        )
    if len(owned) == 1:
        sheet = owned[0]
        existing_created_at = routes[0].created_at if routes else now
        gateway.upsert_user_route(
            UserSheetRoute(
                feishu_user_id=user_id,
                sheet_id=sheet.sheet_id,
                sheet_title=sheet.title,
                status="active",
                schema_version=PENDING_SHEET_SCHEMA_VERSION,
                provision_key=_provision_key(user_id),
                provision_operation_id=operation_id,
                created_at=existing_created_at,
                last_used_at=now,
            )
        )
        return PendingSheetResolution(
            status="ready",
            sheet_id=sheet.sheet_id,
            sheet_title=sheet.title,
            feishu_user_id=user_id,
            repaired_route=True,
        )

    gateway.upsert_user_route(
        UserSheetRoute(
            feishu_user_id=user_id,
            sheet_id="",
            sheet_title="",
            status="creating",
            schema_version=PENDING_SHEET_SCHEMA_VERSION,
            provision_key=_provision_key(user_id),
            provision_operation_id=operation_id,
            created_at=now,
            last_used_at=now,
        )
    )
    try:
        created = gateway.create_pending_sheet_from_template(
            title=_sheet_title(identity),
            template_sheet_id=_required(template_sheet_id, "template_sheet_id"),
            provision_operation_id=operation_id,
        )
        metadata = PendingSheetMetadata(
            owner_user_id=user_id,
            schema_version=PENDING_SHEET_SCHEMA_VERSION,
            provision_key=_provision_key(user_id),
            created_by_operation=operation_id,
        )
        gateway.write_pending_sheet_metadata(created.sheet_id, metadata)
    except Exception as exc:
        return PendingSheetResolution(
            status="failed",
            feishu_user_id=user_id,
            code="USER_PENDING_SHEET_CREATE_FAILED",
            reason=str(exc),
        )

    post_create = _valid_owned_sheets(gateway.list_managed_pending_sheets(), user_id)
    if len(post_create) != 1:
        if len(post_create) > 1:
            return _conflict_resolution(
                gateway,
                identity,
                post_create,
                provision_operation_id=operation_id,
                now=now,
            )
        return PendingSheetResolution(
            status="failed",
            feishu_user_id=user_id,
            code="USER_PENDING_SHEET_CREATE_NOT_VERIFIED",
            reason="创建后无法回读到包含当前用户元数据的Sheet。",
        )
    sheet = post_create[0]
    gateway.upsert_user_route(
        UserSheetRoute(
            feishu_user_id=user_id,
            sheet_id=sheet.sheet_id,
            sheet_title=sheet.title,
            status="active",
            schema_version=PENDING_SHEET_SCHEMA_VERSION,
            provision_key=_provision_key(user_id),
            provision_operation_id=operation_id,
            created_at=now,
            last_used_at=now,
        )
    )
    return PendingSheetResolution(
        status="ready",
        sheet_id=sheet.sheet_id,
        sheet_title=sheet.title,
        feishu_user_id=user_id,
        created=True,
    )


def _same_candidate(
    left: PendingMappingProposal,
    right: PendingMappingProposal,
) -> bool:
    return (
        left.mapping_key == right.mapping_key
        and _normalized(left.candidate_product_code)
        == _normalized(right.candidate_product_code)
        and _normalized(left.candidate_spec_code)
        == _normalized(right.candidate_spec_code)
    )


def submit_pending_mapping_proposal(
    gateway: FeishuSheetGateway,
    proposal: PendingMappingProposal,
    *,
    template_sheet_id: str,
) -> ProposalSubmissionResult:
    identity = gateway.get_current_identity()
    if identity.identity_type != "user" or identity.user_id != proposal.submitted_by:
        return ProposalSubmissionResult(
            status="failed",
            proposal_id=proposal.proposal_id,
            code="PROPOSAL_SUBMITTER_IDENTITY_MISMATCH",
            reason="提案提交人必须与当前飞书用户身份一致。",
        )
    resolution = resolve_or_provision_user_sheet(
        gateway,
        template_sheet_id=template_sheet_id,
        provision_operation_id=proposal.submission_operation_id,
        timestamp=proposal.submitted_at,
    )
    if resolution.status != "ready" or not resolution.sheet_id:
        return ProposalSubmissionResult(
            status=resolution.status,
            proposal_id=proposal.proposal_id,
            code=resolution.code,
            reason=resolution.reason,
        )

    existing = gateway.list_pending_proposals(resolution.sheet_id)
    same_id = [item for item in existing if item.proposal_id == proposal.proposal_id]
    if len(same_id) == 1 and same_id[0] == proposal:
        return ProposalSubmissionResult(
            status="already_submitted",
            proposal_id=proposal.proposal_id,
            sheet_id=resolution.sheet_id,
        )
    if same_id:
        return ProposalSubmissionResult(
            status="needs_input",
            proposal_id=proposal.proposal_id,
            sheet_id=resolution.sheet_id,
            code="PROPOSAL_ID_CONFLICT",
            reason="同一proposal_id对应不同内容。",
        )

    same_mapping = [item for item in existing if item.mapping_key == proposal.mapping_key]
    if any(_same_candidate(item, proposal) for item in same_mapping):
        return ProposalSubmissionResult(
            status="already_pending",
            proposal_id=proposal.proposal_id,
            sheet_id=resolution.sheet_id,
        )
    to_append = proposal
    expected_status = "submitted"
    if same_mapping:
        to_append = replace(proposal, proposal_status="conflict")
        expected_status = "needs_input"
    gateway.append_pending_proposal(resolution.sheet_id, to_append)

    readback = [
        item
        for item in gateway.list_pending_proposals(resolution.sheet_id)
        if item.proposal_id == to_append.proposal_id
    ]
    if len(readback) != 1 or readback[0] != to_append:
        return ProposalSubmissionResult(
            status="failed",
            proposal_id=proposal.proposal_id,
            sheet_id=resolution.sheet_id,
            code="PROPOSAL_WRITE_NOT_VERIFIED",
            reason="写入后无法唯一回读到完全一致的提案。",
        )
    if expected_status == "needs_input":
        return ProposalSubmissionResult(
            status="needs_input",
            proposal_id=proposal.proposal_id,
            sheet_id=resolution.sheet_id,
            code="PENDING_MAPPING_CONFLICT",
            reason="同一用户Sheet中已有相同mapping_key但目标商品不同的候选。",
        )
    return ProposalSubmissionResult(
        status="submitted",
        proposal_id=proposal.proposal_id,
        sheet_id=resolution.sheet_id,
    )
