from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from auto_shipped.catalog import ProductCatalog, clean_identifier

from .official_mappings import OfficialMappingSyncError, build_official_mapping_snapshot
from .pending_mappings import (
    FeishuIdentity,
    ManagedPendingSheet,
    PendingMappingProposal,
)


PUBLISH_CONFIRMATION_TOKEN = "CONFIRM_PUBLISH_ALL_PENDING_MAPPINGS"


class OwnerMappingPublishError(ValueError):
    """Raised when an owner publication cannot be proven safe."""


@dataclass(frozen=True, slots=True)
class OfficialMappingRecord:
    mapping_id: str
    mapping_key: str
    company_id: str
    source_profile_id: str
    identifier_type: str
    source_value: str
    source_spec: str
    target_platform: str
    product_code: str
    spec_code: str
    status: str
    mapping_version: str
    confirmed_by: str
    confirmed_at: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MappingPublishLogRecord:
    event_id: str
    operation_id: str
    action: str
    mapping_key: str
    old_value: str
    new_value: str
    source_proposal_id: str
    submitted_by: str
    approved_by: str
    reason: str
    official_revision_before: str
    official_revision_after: str
    status: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MappingPublishItem:
    proposal: PendingMappingProposal
    record: OfficialMappingRecord | None
    disposition: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MappingPublishPlan:
    status: str
    operation_id: str
    revision_before: str
    revision_after: str
    owner_user_id: str
    items: tuple[MappingPublishItem, ...]
    code: str = ""
    reason: str = ""

    @property
    def create_count(self) -> int:
        return sum(item.disposition == "create" for item in self.items)

    @property
    def already_published_count(self) -> int:
        return sum(item.disposition == "already_published" for item in self.items)

    @property
    def conflict_count(self) -> int:
        return sum(item.disposition == "conflict" for item in self.items)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation_id": self.operation_id,
            "revision_before": self.revision_before,
            "revision_after": self.revision_after,
            "owner_user_id": self.owner_user_id,
            "proposal_count": len(self.items),
            "create_count": self.create_count,
            "already_published_count": self.already_published_count,
            "conflict_count": self.conflict_count,
            "proposal_ids": [item.proposal.proposal_id for item in self.items],
            "code": self.code,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MappingPublishResult:
    status: str
    operation_id: str
    revision_before: str
    revision_after: str
    created_count: int
    already_published_count: int
    published_proposal_count: int
    code: str = ""
    reason: str = ""


class OwnerMappingPublisherGateway(Protocol):
    def get_current_identity(self) -> FeishuIdentity: ...

    def list_managed_pending_sheets(self) -> list[ManagedPendingSheet]: ...

    def list_pending_proposals(self, sheet_id: str) -> list[PendingMappingProposal]: ...

    def update_pending_proposal_status(
        self,
        sheet_id: str,
        proposal_id: str,
        status: str,
    ) -> None: ...

    def get_official_repository_metadata(self) -> dict[str, str]: ...

    def update_official_repository_metadata(
        self,
        updates: Mapping[str, str],
    ) -> None: ...

    def list_official_mapping_records(self) -> list[dict[str, str]]: ...

    def upsert_official_mapping_record(self, record: OfficialMappingRecord) -> None: ...

    def update_official_mapping_status(self, mapping_key: str, status: str) -> None: ...

    def list_mapping_publish_logs(self) -> list[dict[str, str]]: ...

    def upsert_mapping_publish_log(self, record: MappingPublishLogRecord) -> None: ...

    def update_mapping_publish_log_status(self, event_id: str, status: str) -> None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:24]}"


def _candidate(record: Mapping[str, Any]) -> tuple[str, str]:
    return (
        clean_identifier(record.get("product_code")),
        clean_identifier(record.get("spec_code")),
    )


def _proposal_candidate(proposal: PendingMappingProposal) -> tuple[str, str]:
    return (
        clean_identifier(proposal.candidate_product_code),
        clean_identifier(proposal.candidate_spec_code),
    )


def _next_revision(revision: str) -> str:
    value = clean_identifier(revision)
    if not value.isdigit():
        raise OwnerMappingPublishError("正式映射库版本不是整数，拒绝发布。")
    return str(int(value) + 1)


def _gather_pending(
    gateway: OwnerMappingPublisherGateway,
) -> list[tuple[str, PendingMappingProposal]]:
    gathered: list[tuple[str, PendingMappingProposal]] = []
    for sheet in gateway.list_managed_pending_sheets():
        if sheet.metadata is None or sheet.metadata.status != "active":
            continue
        for proposal in gateway.list_pending_proposals(sheet.sheet_id):
            if proposal.proposal_status == "pending":
                gathered.append((sheet.sheet_id, proposal))
    gathered.sort(key=lambda item: (item[1].submitted_at, item[1].proposal_id))
    return gathered


def plan_pending_mapping_publish(
    gateway: OwnerMappingPublisherGateway,
    catalog: ProductCatalog,
    *,
    allowed_owner_user_ids: Sequence[str],
    allowed_company_ids: Sequence[str],
    allowed_source_profile_ids: Sequence[str],
    operation_id: str | None = None,
    timestamp: str | None = None,
) -> MappingPublishPlan:
    identity = gateway.get_current_identity()
    owners = {clean_identifier(value) for value in allowed_owner_user_ids if value}
    if identity.identity_type != "user" or identity.user_id not in owners:
        return MappingPublishPlan(
            status="blocked",
            operation_id=operation_id or "",
            revision_before="",
            revision_after="",
            owner_user_id=identity.user_id,
            items=(),
            code="OWNER_PUBLISHER_IDENTITY_REQUIRED",
            reason="当前飞书用户不在正式映射发布者白名单中。",
        )

    metadata = gateway.get_official_repository_metadata()
    repository_status = clean_identifier(metadata.get("repository_status"))
    revision_before = clean_identifier(metadata.get("mapping_revision"))
    if repository_status != "active":
        return MappingPublishPlan(
            status="blocked",
            operation_id=operation_id or clean_identifier(
                metadata.get("publish_operation_id")
            ),
            revision_before=revision_before,
            revision_after="",
            owner_user_id=identity.user_id,
            items=(),
            code="OFFICIAL_REPOSITORY_NOT_ACTIVE",
            reason=(
                "正式映射库当前不是active。若上次发布中断，应由所有者按"
                "publish_operation_id核对已写记录和日志后恢复；不要启动新的发布。"
            ),
        )
    revision_after = _next_revision(revision_before)

    gathered = _gather_pending(gateway)
    if not gathered:
        return MappingPublishPlan(
            status="no_changes",
            operation_id=operation_id or "",
            revision_before=revision_before,
            revision_after=revision_before,
            owner_user_id=identity.user_id,
            items=(),
        )

    proposal_ids = [proposal.proposal_id for _, proposal in gathered]
    stable_operation_id = operation_id or _stable_id(
        "publish",
        revision_before,
        *sorted(proposal_ids),
    )
    published_at = timestamp or _now()
    official_records = gateway.list_official_mapping_records()
    official_by_key = {
        clean_identifier(record.get("mapping_key")): record
        for record in official_records
        if clean_identifier(record.get("mapping_key"))
    }
    allowed_companies = {clean_identifier(value) for value in allowed_company_ids}
    allowed_profiles = {
        clean_identifier(value) for value in allowed_source_profile_ids
    }

    items: list[MappingPublishItem] = []
    proposal_targets: dict[str, tuple[str, str]] = {}
    duplicate_proposal_ids: dict[str, list[str]] = {}
    for _, proposal in gathered:
        candidate = _proposal_candidate(proposal)
        duplicate_proposal_ids.setdefault(proposal.mapping_key, []).append(
            proposal.proposal_id
        )
        previous_candidate = proposal_targets.get(proposal.mapping_key)
        if previous_candidate is not None and previous_candidate != candidate:
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition="conflict",
                    reason="待确认库中同一mapping_key存在不同目标商品。",
                )
            )
            continue
        proposal_targets.setdefault(proposal.mapping_key, candidate)

        if proposal.proposal_status != "pending":
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition="conflict",
                    reason="只有pending提案允许发布。",
                )
            )
            continue
        if proposal.company_id not in allowed_companies:
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition="conflict",
                    reason="提案公司未登记在公司库。",
                )
            )
            continue
        if proposal.source_profile_id not in allowed_profiles:
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition="conflict",
                    reason="提案来源配置未登记。",
                )
            )
            continue
        if proposal.target_platform != "guanyi":
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition="conflict",
                    reason="当前发布器只允许发布到guanyi。",
                )
            )
            continue
        if not catalog.has_unique_pair(*candidate):
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition="conflict",
                    reason="候选商品代码与规格代码在管易商品主档中不存在或不唯一。",
                )
            )
            continue

        existing = official_by_key.get(proposal.mapping_key)
        if existing is not None:
            disposition = (
                "already_published"
                if _candidate(existing) == candidate
                else "conflict"
            )
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition=disposition,
                    reason=(
                        "正式库已存在完全相同映射。"
                        if disposition == "already_published"
                        else "正式库同一mapping_key已指向不同商品，禁止覆盖。"
                    ),
                )
            )
            continue

        record = OfficialMappingRecord(
            mapping_id=_stable_id("mapping", proposal.mapping_key),
            mapping_key=proposal.mapping_key,
            company_id=proposal.company_id,
            source_profile_id=proposal.source_profile_id,
            identifier_type=proposal.identifier_type,
            source_value=proposal.source_value,
            source_spec=proposal.source_spec,
            target_platform=proposal.target_platform,
            product_code=proposal.candidate_product_code,
            spec_code=proposal.candidate_spec_code,
            status="active",
            mapping_version=revision_after,
            confirmed_by=identity.user_id,
            confirmed_at=published_at,
            evidence=proposal.evidence,
        )
        items.append(
            MappingPublishItem(
                proposal=proposal,
                record=record,
                disposition="create",
            )
        )

    duplicated_same_target = {
        key: ids for key, ids in duplicate_proposal_ids.items() if len(ids) > 1
    }
    for key, proposal_ids_for_key in duplicated_same_target.items():
        candidates = {
            _proposal_candidate(item.proposal)
            for item in items
            if item.proposal.mapping_key == key
        }
        if len(candidates) == 1:
            first = proposal_ids_for_key[0]
            items = [
                replace(
                    item,
                    record=None,
                    disposition="already_published",
                    reason="同批次存在相同目标的重复提案；只发布最早一条。",
                )
                if item.proposal.mapping_key == key
                and item.proposal.proposal_id != first
                else item
                for item in items
            ]

    conflict_count = sum(item.disposition == "conflict" for item in items)
    if conflict_count:
        return MappingPublishPlan(
            status="blocked",
            operation_id=stable_operation_id,
            revision_before=revision_before,
            revision_after=revision_after,
            owner_user_id=identity.user_id,
            items=tuple(items),
            code="MAPPING_PUBLISH_CONFLICT",
            reason="发布前检查发现冲突，整批未写入正式库。",
        )

    combined = [dict(record) for record in official_records]
    combined.extend(
        item.record.to_dict()
        for item in items
        if item.disposition == "create" and item.record is not None
    )
    try:
        build_official_mapping_snapshot(
            combined,
            {
                **metadata,
                "repository_status": "active",
                "mapping_revision": revision_after,
            },
            catalog,
            synced_at=published_at,
        )
    except OfficialMappingSyncError as exc:
        return MappingPublishPlan(
            status="blocked",
            operation_id=stable_operation_id,
            revision_before=revision_before,
            revision_after=revision_after,
            owner_user_id=identity.user_id,
            items=tuple(items),
            code="MAPPING_PUBLISH_SNAPSHOT_INVALID",
            reason=str(exc),
        )

    return MappingPublishPlan(
        status="ready",
        operation_id=stable_operation_id,
        revision_before=revision_before,
        revision_after=(
            revision_after
            if any(item.disposition == "create" for item in items)
            else revision_before
        ),
        owner_user_id=identity.user_id,
        items=tuple(items),
    )


def publish_pending_mappings(
    gateway: OwnerMappingPublisherGateway,
    catalog: ProductCatalog,
    *,
    allowed_owner_user_ids: Sequence[str],
    allowed_company_ids: Sequence[str],
    allowed_source_profile_ids: Sequence[str],
    confirmation_token: str,
    operation_id: str | None = None,
    timestamp: str | None = None,
) -> MappingPublishResult:
    if confirmation_token != PUBLISH_CONFIRMATION_TOKEN:
        return MappingPublishResult(
            status="needs_confirmation",
            operation_id=operation_id or "",
            revision_before="",
            revision_after="",
            created_count=0,
            already_published_count=0,
            published_proposal_count=0,
            code="MAPPING_PUBLISH_CONFIRMATION_REQUIRED",
            reason="发布正式映射需要所有者明确确认。",
        )

    published_at = timestamp or _now()
    plan = plan_pending_mapping_publish(
        gateway,
        catalog,
        allowed_owner_user_ids=allowed_owner_user_ids,
        allowed_company_ids=allowed_company_ids,
        allowed_source_profile_ids=allowed_source_profile_ids,
        operation_id=operation_id,
        timestamp=published_at,
    )
    if plan.status == "no_changes":
        return MappingPublishResult(
            status="no_changes",
            operation_id=plan.operation_id,
            revision_before=plan.revision_before,
            revision_after=plan.revision_after,
            created_count=0,
            already_published_count=0,
            published_proposal_count=0,
        )
    if plan.status != "ready":
        return MappingPublishResult(
            status="blocked",
            operation_id=plan.operation_id,
            revision_before=plan.revision_before,
            revision_after=plan.revision_after,
            created_count=0,
            already_published_count=plan.already_published_count,
            published_proposal_count=0,
            code=plan.code,
            reason=plan.reason,
        )

    metadata = gateway.get_official_repository_metadata()
    if (
        clean_identifier(metadata.get("repository_status")) != "active"
        or clean_identifier(metadata.get("mapping_revision"))
        != plan.revision_before
    ):
        return MappingPublishResult(
            status="blocked",
            operation_id=plan.operation_id,
            revision_before=plan.revision_before,
            revision_after=plan.revision_after,
            created_count=0,
            already_published_count=plan.already_published_count,
            published_proposal_count=0,
            code="MAPPING_PUBLISH_REVISION_CHANGED",
            reason="正式映射库在预检后发生变化，整批未发布。",
        )

    create_items = [
        item
        for item in plan.items
        if item.disposition == "create" and item.record is not None
    ]
    try:
        if create_items:
            gateway.update_official_repository_metadata(
                {
                    "repository_status": "publishing",
                    "publish_operation_id": plan.operation_id,
                }
            )
            for item in create_items:
                assert item.record is not None
                gateway.upsert_official_mapping_record(
                    replace(item.record, status="inactive")
                )
                log = MappingPublishLogRecord(
                    event_id=_stable_id(
                        "event",
                        plan.operation_id,
                        item.proposal.proposal_id,
                    ),
                    operation_id=plan.operation_id,
                    action="CREATE",
                    mapping_key=item.proposal.mapping_key,
                    old_value="{}",
                    new_value=json.dumps(
                        {
                            "product_code": item.proposal.candidate_product_code,
                            "spec_code": item.proposal.candidate_spec_code,
                            "status": "active",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    source_proposal_id=item.proposal.proposal_id,
                    submitted_by=item.proposal.submitted_by,
                    approved_by=plan.owner_user_id,
                    reason=item.proposal.evidence,
                    official_revision_before=plan.revision_before,
                    official_revision_after=plan.revision_after,
                    status="staged",
                    created_at=published_at,
                )
                gateway.upsert_mapping_publish_log(log)

            for item in create_items:
                gateway.update_official_mapping_status(
                    item.proposal.mapping_key,
                    "active",
                )
            gateway.update_official_repository_metadata(
                {
                    "mapping_revision": plan.revision_after,
                    "last_published_at": published_at,
                    "last_published_by": plan.owner_user_id,
                }
            )
            combined = gateway.list_official_mapping_records()
            verify_metadata = gateway.get_official_repository_metadata()
            build_official_mapping_snapshot(
                combined,
                {
                    **verify_metadata,
                    "repository_status": "active",
                },
                catalog,
                synced_at=published_at,
            )
            gateway.update_official_repository_metadata(
                {
                    "repository_status": "active",
                    "publish_operation_id": "",
                }
            )
            for item in create_items:
                event_id = _stable_id(
                    "event",
                    plan.operation_id,
                    item.proposal.proposal_id,
                )
                gateway.update_mapping_publish_log_status(event_id, "published")

        for sheet in gateway.list_managed_pending_sheets():
            proposal_ids = {
                item.proposal.proposal_id
                for item in plan.items
                if item.disposition in {"create", "already_published"}
            }
            for proposal in gateway.list_pending_proposals(sheet.sheet_id):
                if proposal.proposal_id in proposal_ids:
                    gateway.update_pending_proposal_status(
                        sheet.sheet_id,
                        proposal.proposal_id,
                        "published",
                    )
    except Exception as exc:
        return MappingPublishResult(
            status="failed_closed",
            operation_id=plan.operation_id,
            revision_before=plan.revision_before,
            revision_after=plan.revision_after,
            created_count=0,
            already_published_count=plan.already_published_count,
            published_proposal_count=0,
            code="MAPPING_PUBLISH_INTERRUPTED",
            reason=(
                "发布中断；正式库保持非active或可核验状态，禁止转换，"
                f"需要所有者按operation_id={plan.operation_id}核对并恢复：{exc}"
            ),
        )

    return MappingPublishResult(
        status="published",
        operation_id=plan.operation_id,
        revision_before=plan.revision_before,
        revision_after=plan.revision_after,
        created_count=plan.create_count,
        already_published_count=plan.already_published_count,
        published_proposal_count=len(plan.items),
    )
