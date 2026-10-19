from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from auto_shipped.catalog import ProductCatalog, clean_identifier

from .official_mappings import OfficialMappingSyncError, build_official_mapping_snapshot
from .manual_store_routes import (
    ManualStoreRouteRecord,
    build_manual_store_route_key,
)
from .item_expansion_registry import (
    ItemExpansionComponentRecord,
    ItemExpansionRegistryError,
    build_item_expansion_rule_key,
    normalize_item_expansion_rules,
)
from .pending_mappings import (
    FeishuIdentity,
    ManagedPendingSheet,
    PendingMappingProposal,
    build_mapping_key,
)
from .mapping_contract import MappingScopeError, scoped_mapping_identity


PUBLISH_CONFIRMATION_TOKEN = "CONFIRM_PUBLISH_ALL_PENDING_MAPPINGS"
DIRECT_ADD_CONFIRMATION_TOKEN = "CONFIRM_ADD_NEW_OFFICIAL_MAPPING"
DIRECT_STORE_ROUTE_CONFIRMATION_TOKEN = "CONFIRM_ADD_MANUAL_STORE_ROUTE"
DIRECT_ITEM_EXPANSION_CONFIRMATION_TOKEN = "CONFIRM_ADD_ITEM_EXPANSION_RULE"
REPOSITORY_RECOVERY_CONFIRMATION_TOKEN = "CONFIRM_RECOVER_OFFICIAL_REPOSITORY"


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
    canonical_source_brand: str = ""
    canonical_source_product: str = ""
    canonical_source_spec: str = ""
    canonical_schema_version: str = ""

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


@dataclass(frozen=True, slots=True)
class DirectMappingAddResult:
    status: str
    operation_id: str
    mapping_key: str
    revision_before: str
    revision_after: str
    product_code: str
    spec_code: str
    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DirectManualStoreRouteAddResult:
    status: str
    operation_id: str
    route_key: str
    revision_before: str
    revision_after: str
    product_code: str
    spec_code: str
    store: str
    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DirectItemExpansionAddResult:
    status: str
    operation_id: str
    rule_key: str
    rule_id: str
    revision_before: str
    revision_after: str
    component_count: int
    code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OfficialRepositoryRecoveryResult:
    status: str
    operation_id: str
    repository_status: str
    mapping_revision: str
    log_count: int
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

    def list_manual_store_route_records(self) -> list[dict[str, str]]: ...

    def upsert_manual_store_route_record(
        self,
        record: ManualStoreRouteRecord,
    ) -> None: ...

    def update_manual_store_route_status(
        self,
        route_key: str,
        status: str,
    ) -> None: ...

    def list_item_expansion_component_records(self) -> list[dict[str, str]]: ...

    def upsert_item_expansion_component_records(
        self,
        records: Sequence[ItemExpansionComponentRecord],
    ) -> None: ...

    def update_item_expansion_rule_status(
        self,
        rule_key: str,
        status: str,
    ) -> None: ...

    def upsert_official_mapping_record(self, record: OfficialMappingRecord) -> None: ...

    def update_official_mapping_status(self, mapping_key: str, status: str) -> None: ...

    def list_mapping_publish_logs(self) -> list[dict[str, str]]: ...

    def upsert_mapping_publish_log(self, record: MappingPublishLogRecord) -> None: ...

    def update_mapping_publish_log_status(self, event_id: str, status: str) -> None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manual_store_routes(gateway: OwnerMappingPublisherGateway) -> list[dict[str, str]]:
    reader = getattr(gateway, "list_manual_store_route_records", None)
    return list(reader()) if callable(reader) else []


def _item_expansion_records(
    gateway: OwnerMappingPublisherGateway,
) -> list[dict[str, str]]:
    reader = getattr(gateway, "list_item_expansion_component_records", None)
    return list(reader()) if callable(reader) else []


def diagnose_official_repository_recovery(
    gateway: OwnerMappingPublisherGateway,
    catalog: ProductCatalog,
) -> OfficialRepositoryRecoveryResult:
    """Prove whether an interrupted publish only needs its final unlock step."""

    metadata = gateway.get_official_repository_metadata()
    repository_status = clean_identifier(metadata.get("repository_status"))
    revision = clean_identifier(metadata.get("mapping_revision"))
    operation_id = clean_identifier(metadata.get("publish_operation_id"))
    if repository_status == "active":
        return OfficialRepositoryRecoveryResult(
            status="healthy",
            operation_id="",
            repository_status=repository_status,
            mapping_revision=revision,
            log_count=0,
            reason="正式库已经是active，无需恢复。",
        )
    if repository_status != "publishing" or not operation_id:
        return OfficialRepositoryRecoveryResult(
            status="blocked",
            operation_id=operation_id,
            repository_status=repository_status,
            mapping_revision=revision,
            log_count=0,
            code="OFFICIAL_REPOSITORY_RECOVERY_METADATA_INVALID",
            reason="只有带publish_operation_id的publishing状态可自动诊断。",
        )

    logs = [
        item
        for item in gateway.list_mapping_publish_logs()
        if clean_identifier(item.get("operation_id")) == operation_id
    ]
    after_revisions = {
        clean_identifier(item.get("official_revision_after")) for item in logs
    }
    if not logs or len(after_revisions) != 1 or revision not in after_revisions:
        return OfficialRepositoryRecoveryResult(
            status="blocked",
            operation_id=operation_id,
            repository_status=repository_status,
            mapping_revision=revision,
            log_count=len(logs),
            code="OFFICIAL_REPOSITORY_RECOVERY_REVISION_UNPROVEN",
            reason="发布日志不存在、目标版本不唯一，或元数据尚未到达目标版本。",
        )
    if any(clean_identifier(item.get("status")) not in {"staged", "published"} for item in logs):
        return OfficialRepositoryRecoveryResult(
            status="blocked",
            operation_id=operation_id,
            repository_status=repository_status,
            mapping_revision=revision,
            log_count=len(logs),
            code="OFFICIAL_REPOSITORY_RECOVERY_LOG_INVALID",
            reason="发布日志含有无法识别的状态。",
        )

    mappings = gateway.list_official_mapping_records()
    routes = _manual_store_routes(gateway)
    expansions = _item_expansion_records(gateway)
    mapping_by_key = {
        clean_identifier(item.get("mapping_key")): item for item in mappings
    }
    route_by_key = {
        clean_identifier(item.get("route_key")): item for item in routes
    }
    expansion_by_key: dict[str, list[dict[str, str]]] = {}
    for item in expansions:
        expansion_by_key.setdefault(clean_identifier(item.get("rule_key")), []).append(item)
    for log in logs:
        action = clean_identifier(log.get("action"))
        key = clean_identifier(log.get("mapping_key"))
        if action in {"CREATE", "DIRECT_CREATE"}:
            records = [mapping_by_key.get(key)]
        elif action == "DIRECT_CREATE_MANUAL_STORE_ROUTE":
            records = [route_by_key.get(key)]
        elif action == "DIRECT_CREATE_ITEM_EXPANSION":
            records = expansion_by_key.get(key, [])
        else:
            records = []
        if not records or any(
            record is None or clean_identifier(record.get("status")) != "active"
            for record in records
        ):
            return OfficialRepositoryRecoveryResult(
                status="blocked",
                operation_id=operation_id,
                repository_status=repository_status,
                mapping_revision=revision,
                log_count=len(logs),
                code="OFFICIAL_REPOSITORY_RECOVERY_TARGET_INCOMPLETE",
                reason=f"操作{action or 'unknown'}的目标记录尚未全部处于active。",
            )
    try:
        build_official_mapping_snapshot(
            mappings,
            {**metadata, "repository_status": "active"},
            catalog,
            manual_store_route_records=routes,
            item_expansion_records=expansions,
            synced_at=_now(),
        )
    except OfficialMappingSyncError as exc:
        return OfficialRepositoryRecoveryResult(
            status="blocked",
            operation_id=operation_id,
            repository_status=repository_status,
            mapping_revision=revision,
            log_count=len(logs),
            code="OFFICIAL_REPOSITORY_RECOVERY_SNAPSHOT_INVALID",
            reason=str(exc),
        )
    return OfficialRepositoryRecoveryResult(
        status="recoverable",
        operation_id=operation_id,
        repository_status=repository_status,
        mapping_revision=revision,
        log_count=len(logs),
        reason="目标记录、发布日志、版本号和完整快照一致；只需完成日志封口并解锁。",
    )


def recover_official_repository(
    gateway: OwnerMappingPublisherGateway,
    catalog: ProductCatalog,
    *,
    allowed_owner_user_ids: Sequence[str],
    confirmation_token: str,
) -> OfficialRepositoryRecoveryResult:
    identity = gateway.get_current_identity()
    owners = {clean_identifier(value) for value in allowed_owner_user_ids if value}
    if identity.identity_type != "user" or identity.user_id not in owners:
        return OfficialRepositoryRecoveryResult(
            status="blocked",
            operation_id="",
            repository_status="",
            mapping_revision="",
            log_count=0,
            code="OWNER_PUBLISHER_IDENTITY_REQUIRED",
            reason="只有正式库所有者可以执行恢复。",
        )
    if confirmation_token != REPOSITORY_RECOVERY_CONFIRMATION_TOKEN:
        return OfficialRepositoryRecoveryResult(
            status="blocked",
            operation_id="",
            repository_status="",
            mapping_revision="",
            log_count=0,
            code="OFFICIAL_REPOSITORY_RECOVERY_CONFIRMATION_REQUIRED",
            reason="缺少正式库恢复确认令牌。",
        )
    diagnosis = diagnose_official_repository_recovery(gateway, catalog)
    if diagnosis.status == "healthy":
        return diagnosis
    if diagnosis.status != "recoverable":
        return diagnosis
    try:
        for log in gateway.list_mapping_publish_logs():
            if clean_identifier(log.get("operation_id")) != diagnosis.operation_id:
                continue
            if clean_identifier(log.get("status")) == "staged":
                gateway.update_mapping_publish_log_status(
                    clean_identifier(log.get("event_id")),
                    "published",
                )
        gateway.update_official_repository_metadata(
            {"repository_status": "active", "publish_operation_id": ""}
        )
        verified = gateway.get_official_repository_metadata()
        if (
            clean_identifier(verified.get("repository_status")) != "active"
            or clean_identifier(verified.get("publish_operation_id"))
        ):
            raise OwnerMappingPublishError("恢复写入后元数据回读不一致。")
    except Exception as exc:
        return replace(
            diagnosis,
            status="failed_closed",
            code="OFFICIAL_REPOSITORY_RECOVERY_WRITE_FAILED",
            reason=str(exc),
        )
    return replace(
        diagnosis,
        status="recovered",
        repository_status="active",
        reason="发布日志已封口，正式库已恢复active。",
    )


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:24]}"


def _candidate(record: Mapping[str, Any]) -> tuple[str, str]:
    return (
        clean_identifier(record.get("product_code")),
        clean_identifier(record.get("spec_code")),
    )


def _canonical_source_fields(
    catalog: ProductCatalog,
    *,
    identifier_type: str,
    source_value: str,
    source_spec: str,
) -> dict[str, str]:
    if clean_identifier(identifier_type) != "product_name":
        return {
            "canonical_source_brand": "",
            "canonical_source_product": "",
            "canonical_source_spec": "",
            "canonical_schema_version": "",
        }
    identity = catalog.canonicalize_source_identity(
        source_product_name=source_value,
        source_spec=source_spec,
    )
    if not identity.product or any(
        issue.endswith("_ambiguous") for issue in identity.issues
    ):
        raise OwnerMappingPublishError(
            "商品名称映射无法生成唯一规范商品主体，拒绝写入正式映射。"
        )
    return {
        "canonical_source_brand": identity.brand,
        "canonical_source_product": identity.product,
        "canonical_source_spec": identity.spec,
        "canonical_schema_version": identity.schema_version,
    }


def _proposal_candidate(proposal: PendingMappingProposal) -> tuple[str, str]:
    return (
        clean_identifier(proposal.candidate_product_code),
        clean_identifier(proposal.candidate_spec_code),
    )


def _canonical_proposal_candidate(
    catalog: ProductCatalog,
    proposal: PendingMappingProposal,
) -> tuple[str, str]:
    submitted = _proposal_candidate(proposal)
    resolved = catalog.resolve_unique_target(*submitted)
    if resolved is None:
        return submitted
    return resolved.product_code, resolved.spec_code


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


def add_confirmed_mapping_direct(
    gateway: OwnerMappingPublisherGateway,
    catalog: ProductCatalog,
    *,
    allowed_company_ids: Sequence[str],
    allowed_source_profile_ids: Sequence[str],
    company_id: str,
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
    target_platform: str,
    product_code: str,
    spec_code: str,
    evidence: str,
    confirmation_token: str,
    operation_id: str | None = None,
    timestamp: str | None = None,
    mapping_scope: str = "source",
) -> DirectMappingAddResult:
    """Append one conflict-free user-confirmed mapping to the official table.

    This path is deliberately create-only. It never replaces, deletes, merges,
    or re-keys an existing official mapping.
    """

    company_id = clean_identifier(company_id)
    source_profile_id = clean_identifier(source_profile_id)
    identifier_type = clean_identifier(identifier_type)
    source_value = clean_identifier(source_value)
    source_spec = clean_identifier(source_spec)
    target_platform = clean_identifier(target_platform)
    evidence = clean_identifier(evidence)
    if confirmation_token != DIRECT_ADD_CONFIRMATION_TOKEN:
        return DirectMappingAddResult(
            status="needs_confirmation",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_ADD_CONFIRMATION_REQUIRED",
            reason="新增正式映射前必须取得用户明确的‘以后沿用’确认。",
        )
    identity = gateway.get_current_identity()
    if identity.identity_type != "user" or not clean_identifier(identity.user_id):
        return DirectMappingAddResult(
            status="blocked",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_USER_IDENTITY_REQUIRED",
            reason="必须使用可追溯的飞书用户身份，禁止共享机器人身份写正式映射。",
        )
    try:
        company_id, source_profile_id, resolved_scope = scoped_mapping_identity(
            mapping_scope=mapping_scope,
            company_id=company_id,
            source_profile_id=source_profile_id,
            identifier_type=identifier_type,
            source_value=source_value,
        )
    except MappingScopeError as exc:
        return DirectMappingAddResult(
            status="blocked",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_SCOPE_INVALID",
            reason=str(exc),
        )
    if resolved_scope == "source" and company_id not in {
        clean_identifier(value) for value in allowed_company_ids
    }:
        return DirectMappingAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_COMPANY_NOT_REGISTERED",
            reason="公司尚未登记，不能通过商品映射入口新增；请联系管理员维护公司库。",
        )
    if resolved_scope == "source" and source_profile_id not in {
        clean_identifier(value) for value in allowed_source_profile_ids
    }:
        return DirectMappingAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_SOURCE_PROFILE_NOT_REGISTERED",
            reason="来源配置尚未登记，请联系管理员维护来源配置。",
        )
    if target_platform != "guanyi":
        return DirectMappingAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_TARGET_PLATFORM_UNSUPPORTED",
            reason="当前受控新增入口只支持管易商品映射。",
        )
    if identifier_type not in {"product_code", "barcode", "product_name"}:
        return DirectMappingAddResult(
            status="blocked",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_IDENTIFIER_TYPE_INVALID",
            reason="来源商品标识类型不受支持。",
        )
    if not evidence:
        return DirectMappingAddResult(
            status="blocked",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_EVIDENCE_REQUIRED",
            reason="必须记录用户确认依据。",
        )
    try:
        mapping_key = build_mapping_key(
            company_id=company_id,
            source_profile_id=source_profile_id,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
            target_platform=target_platform,
        )
    except ValueError as exc:
        return DirectMappingAddResult(
            status="blocked",
            operation_id=operation_id or "",
            mapping_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            code="DIRECT_MAPPING_KEY_INVALID",
            reason=str(exc),
        )
    target = catalog.resolve_unique_target(product_code, spec_code)
    if target is None:
        return DirectMappingAddResult(
            status="blocked",
            operation_id=operation_id or "",
            mapping_key=mapping_key,
            revision_before="",
            revision_after="",
            product_code=clean_identifier(product_code),
            spec_code=clean_identifier(spec_code),
            code="DIRECT_MAPPING_TARGET_NOT_UNIQUE",
            reason="目标商品代码与规格代码在有效商品目录中不存在或不唯一。",
        )

    metadata = gateway.get_official_repository_metadata()
    revision_before = clean_identifier(metadata.get("mapping_revision"))
    if clean_identifier(metadata.get("repository_status")) != "active":
        return DirectMappingAddResult(
            status="blocked",
            operation_id=operation_id or clean_identifier(
                metadata.get("publish_operation_id")
            ),
            mapping_key=mapping_key,
            revision_before=revision_before,
            revision_after="",
            product_code=target.product_code,
            spec_code=target.spec_code,
            code="OFFICIAL_REPOSITORY_NOT_ACTIVE",
            reason="正式映射库不是active，需管理员先核对并恢复。",
        )
    revision_after = _next_revision(revision_before)
    official_records = gateway.list_official_mapping_records()
    existing = [
        record
        for record in official_records
        if clean_identifier(record.get("mapping_key")) == mapping_key
    ]
    if len(existing) > 1:
        return DirectMappingAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            mapping_key=mapping_key,
            revision_before=revision_before,
            revision_after=revision_before,
            product_code=target.product_code,
            spec_code=target.spec_code,
            code="DIRECT_MAPPING_DUPLICATE_OFFICIAL_KEY",
            reason="正式映射中同一键存在多条记录，请联系管理员治理。",
        )
    if existing:
        existing_target = _candidate(existing[0])
        if (
            clean_identifier(existing[0].get("status")) == "active"
            and existing_target == (target.product_code, target.spec_code)
        ):
            return DirectMappingAddResult(
                status="already_exists",
                operation_id=operation_id or "",
                mapping_key=mapping_key,
                revision_before=revision_before,
                revision_after=revision_before,
                product_code=target.product_code,
                spec_code=target.spec_code,
                reason="正式库已存在完全相同映射，未重复写入。",
            )
        return DirectMappingAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            mapping_key=mapping_key,
            revision_before=revision_before,
            revision_after=revision_before,
            product_code=target.product_code,
            spec_code=target.spec_code,
            code="DIRECT_MAPPING_CONFLICTS_WITH_OFFICIAL",
            reason="同一映射键已存在不同目标或异常状态，禁止普通用户覆盖；请联系管理员。",
        )

    written_at = timestamp or _now()
    stable_operation_id = operation_id or _stable_id(
        "direct-add",
        revision_before,
        mapping_key,
        target.product_code,
        target.spec_code,
        identity.user_id,
    )
    record = OfficialMappingRecord(
        mapping_id=_stable_id("mapping", mapping_key),
        mapping_key=mapping_key,
        company_id=company_id,
        source_profile_id=source_profile_id,
        identifier_type=identifier_type,
        source_value=source_value,
        source_spec=source_spec,
        target_platform=target_platform,
        product_code=target.product_code,
        spec_code=target.spec_code,
        status="active",
        mapping_version=revision_after,
        confirmed_by=identity.user_id,
        confirmed_at=written_at,
        evidence=evidence,
        **_canonical_source_fields(
            catalog,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
        ),
    )
    try:
        build_official_mapping_snapshot(
            [*official_records, record.to_dict()],
            {
                **metadata,
                "repository_status": "active",
                "mapping_revision": revision_after,
            },
            catalog,
            manual_store_route_records=_manual_store_routes(gateway),
            item_expansion_records=_item_expansion_records(gateway),
            synced_at=written_at,
        )
    except OfficialMappingSyncError as exc:
        return DirectMappingAddResult(
            status="blocked",
            operation_id=stable_operation_id,
            mapping_key=mapping_key,
            revision_before=revision_before,
            revision_after=revision_after,
            product_code=target.product_code,
            spec_code=target.spec_code,
            code="DIRECT_MAPPING_SNAPSHOT_INVALID",
            reason=str(exc),
        )
    try:
        latest = gateway.get_official_repository_metadata()
        if (
            clean_identifier(latest.get("repository_status")) != "active"
            or clean_identifier(latest.get("mapping_revision")) != revision_before
        ):
            return DirectMappingAddResult(
                status="blocked",
                operation_id=stable_operation_id,
                mapping_key=mapping_key,
                revision_before=revision_before,
                revision_after=revision_after,
                product_code=target.product_code,
                spec_code=target.spec_code,
                code="DIRECT_MAPPING_REVISION_CHANGED",
                reason="正式映射库在预检后发生变化，未写入。",
            )
        gateway.update_official_repository_metadata(
            {
                "repository_status": "publishing",
                "publish_operation_id": stable_operation_id,
            }
        )
        gateway.upsert_official_mapping_record(replace(record, status="inactive"))
        event_id = _stable_id("event", stable_operation_id, mapping_key)
        gateway.upsert_mapping_publish_log(
            MappingPublishLogRecord(
                event_id=event_id,
                operation_id=stable_operation_id,
                action="DIRECT_CREATE",
                mapping_key=mapping_key,
                old_value="{}",
                new_value=json.dumps(
                    {
                        "product_code": target.product_code,
                        "spec_code": target.spec_code,
                        "status": "active",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_proposal_id="",
                submitted_by=identity.user_id,
                approved_by=identity.user_id,
                reason=evidence,
                official_revision_before=revision_before,
                official_revision_after=revision_after,
                status="staged",
                created_at=written_at,
            )
        )
        gateway.update_official_mapping_status(mapping_key, "active")
        gateway.update_official_repository_metadata(
            {
                "mapping_revision": revision_after,
                "last_published_at": written_at,
                "last_published_by": identity.user_id,
            }
        )
        verified_records = gateway.list_official_mapping_records()
        verify_metadata = gateway.get_official_repository_metadata()
        build_official_mapping_snapshot(
            verified_records,
            {**verify_metadata, "repository_status": "active"},
            catalog,
            manual_store_route_records=_manual_store_routes(gateway),
            item_expansion_records=_item_expansion_records(gateway),
            synced_at=written_at,
        )
        gateway.update_mapping_publish_log_status(event_id, "published")
        gateway.update_official_repository_metadata(
            {"repository_status": "active", "publish_operation_id": ""}
        )
    except Exception as exc:
        return DirectMappingAddResult(
            status="failed_closed",
            operation_id=stable_operation_id,
            mapping_key=mapping_key,
            revision_before=revision_before,
            revision_after=revision_after,
            product_code=target.product_code,
            spec_code=target.spec_code,
            code="DIRECT_MAPPING_WRITE_INTERRUPTED",
            reason=(
                "写入中断；正式库保持可核验状态，禁止继续转换。请管理员按"
                f"operation_id={stable_operation_id}核对并恢复：{exc}"
            ),
        )
    return DirectMappingAddResult(
        status="created",
        operation_id=stable_operation_id,
        mapping_key=mapping_key,
        revision_before=revision_before,
        revision_after=revision_after,
        product_code=target.product_code,
        spec_code=target.spec_code,
    )


def add_confirmed_manual_store_route_direct(
    gateway: OwnerMappingPublisherGateway,
    catalog: ProductCatalog,
    *,
    allowed_scope_ids: Sequence[str],
    allowed_stores: Sequence[str],
    scope_id: str,
    product_code: str,
    spec_code: str,
    store: str,
    evidence: str,
    confirmation_token: str,
    operation_id: str | None = None,
    timestamp: str | None = None,
) -> DirectManualStoreRouteAddResult:
    """Append one confirmed SKU-to-store route without replacing existing data."""

    scope_id = clean_identifier(scope_id)
    store = clean_identifier(store)
    evidence = clean_identifier(evidence)
    if confirmation_token != DIRECT_STORE_ROUTE_CONFIRMATION_TOKEN:
        return DirectManualStoreRouteAddResult(
            status="needs_confirmation",
            operation_id=operation_id or "",
            route_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            store=store,
            code="DIRECT_STORE_ROUTE_CONFIRMATION_REQUIRED",
            reason="新增手工单商品店铺关联前必须取得用户明确的‘以后沿用’确认。",
        )
    identity = gateway.get_current_identity()
    if identity.identity_type != "user" or not clean_identifier(identity.user_id):
        return DirectManualStoreRouteAddResult(
            status="blocked",
            operation_id=operation_id or "",
            route_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            store=store,
            code="DIRECT_STORE_ROUTE_USER_IDENTITY_REQUIRED",
            reason="必须使用可追溯的飞书用户身份写入正式店铺关联。",
        )
    scopes = {clean_identifier(value) for value in allowed_scope_ids if value}
    if scope_id not in scopes:
        return DirectManualStoreRouteAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            route_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            store=store,
            code="DIRECT_STORE_ROUTE_SCOPE_NOT_REGISTERED",
            reason="该手工订单来源尚未登记，不能直接新增店铺关联。",
        )
    stores = {clean_identifier(value) for value in allowed_stores if value}
    if store not in stores:
        return DirectManualStoreRouteAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            route_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            store=store,
            code="DIRECT_STORE_ROUTE_STORE_NOT_ALLOWED",
            reason="目标店铺不在已登记允许清单中，请联系管理员维护店铺策略。",
        )
    if not evidence:
        return DirectManualStoreRouteAddResult(
            status="blocked",
            operation_id=operation_id or "",
            route_key="",
            revision_before="",
            revision_after="",
            product_code="",
            spec_code="",
            store=store,
            code="DIRECT_STORE_ROUTE_EVIDENCE_REQUIRED",
            reason="必须记录用户确认依据。",
        )
    target = catalog.resolve_unique_target(product_code, spec_code)
    if target is None:
        return DirectManualStoreRouteAddResult(
            status="blocked",
            operation_id=operation_id or "",
            route_key="",
            revision_before="",
            revision_after="",
            product_code=clean_identifier(product_code),
            spec_code=clean_identifier(spec_code),
            store=store,
            code="DIRECT_STORE_ROUTE_TARGET_NOT_UNIQUE",
            reason="目标商品代码与规格代码在有效商品目录中不存在或不唯一。",
        )
    route_key = build_manual_store_route_key(
        scope_id=scope_id,
        product_code=target.product_code,
        spec_code=target.spec_code,
    )
    metadata = gateway.get_official_repository_metadata()
    revision_before = clean_identifier(metadata.get("mapping_revision"))
    if clean_identifier(metadata.get("repository_status")) != "active":
        return DirectManualStoreRouteAddResult(
            status="blocked",
            operation_id=operation_id
            or clean_identifier(metadata.get("publish_operation_id")),
            route_key=route_key,
            revision_before=revision_before,
            revision_after="",
            product_code=target.product_code,
            spec_code=target.spec_code,
            store=store,
            code="OFFICIAL_REPOSITORY_NOT_ACTIVE",
            reason="正式映射库不是active，需管理员先核对并恢复。",
        )
    revision_after = _next_revision(revision_before)
    existing_routes = _manual_store_routes(gateway)
    existing = [
        record
        for record in existing_routes
        if clean_identifier(record.get("route_key")) == route_key
    ]
    if len(existing) > 1:
        return DirectManualStoreRouteAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            route_key=route_key,
            revision_before=revision_before,
            revision_after=revision_before,
            product_code=target.product_code,
            spec_code=target.spec_code,
            store=store,
            code="DIRECT_STORE_ROUTE_DUPLICATE_KEY",
            reason="正式库中同一店铺关联键存在多条记录，请联系管理员治理。",
        )
    if existing:
        current = existing[0]
        if (
            clean_identifier(current.get("status")) == "active"
            and clean_identifier(current.get("store")) == store
        ):
            return DirectManualStoreRouteAddResult(
                status="already_exists",
                operation_id=operation_id or "",
                route_key=route_key,
                revision_before=revision_before,
                revision_after=revision_before,
                product_code=target.product_code,
                spec_code=target.spec_code,
                store=store,
                reason="正式库已存在完全相同店铺关联，未重复写入。",
            )
        return DirectManualStoreRouteAddResult(
            status="needs_admin",
            operation_id=operation_id or "",
            route_key=route_key,
            revision_before=revision_before,
            revision_after=revision_before,
            product_code=target.product_code,
            spec_code=target.spec_code,
            store=store,
            code="DIRECT_STORE_ROUTE_CONFLICTS_WITH_OFFICIAL",
            reason="同一商品和作用域已关联不同店铺或处于异常状态，禁止普通用户覆盖。",
        )

    written_at = timestamp or _now()
    stable_operation_id = operation_id or _stable_id(
        "direct-store-route",
        revision_before,
        route_key,
        store,
        identity.user_id,
    )
    record = ManualStoreRouteRecord(
        route_id=_stable_id("store-route-record", route_key),
        route_key=route_key,
        scope_id=scope_id,
        product_code=target.product_code,
        spec_code=target.spec_code,
        store=store,
        status="active",
        route_version=revision_after,
        confirmed_by=identity.user_id,
        confirmed_at=written_at,
        evidence=evidence,
    )
    official_records = gateway.list_official_mapping_records()
    try:
        build_official_mapping_snapshot(
            official_records,
            {
                **metadata,
                "repository_status": "active",
                "mapping_revision": revision_after,
            },
            catalog,
            manual_store_route_records=[*existing_routes, record.to_dict()],
            item_expansion_records=_item_expansion_records(gateway),
            synced_at=written_at,
        )
    except OfficialMappingSyncError as exc:
        return DirectManualStoreRouteAddResult(
            status="blocked",
            operation_id=stable_operation_id,
            route_key=route_key,
            revision_before=revision_before,
            revision_after=revision_after,
            product_code=target.product_code,
            spec_code=target.spec_code,
            store=store,
            code="DIRECT_STORE_ROUTE_SNAPSHOT_INVALID",
            reason=str(exc),
        )
    try:
        latest = gateway.get_official_repository_metadata()
        if (
            clean_identifier(latest.get("repository_status")) != "active"
            or clean_identifier(latest.get("mapping_revision")) != revision_before
        ):
            return DirectManualStoreRouteAddResult(
                status="blocked",
                operation_id=stable_operation_id,
                route_key=route_key,
                revision_before=revision_before,
                revision_after=revision_after,
                product_code=target.product_code,
                spec_code=target.spec_code,
                store=store,
                code="DIRECT_STORE_ROUTE_REVISION_CHANGED",
                reason="正式映射库在预检后发生变化，未写入。",
            )
        gateway.update_official_repository_metadata(
            {
                "repository_status": "publishing",
                "publish_operation_id": stable_operation_id,
            }
        )
        gateway.upsert_manual_store_route_record(replace(record, status="inactive"))
        event_id = _stable_id("event", stable_operation_id, route_key)
        gateway.upsert_mapping_publish_log(
            MappingPublishLogRecord(
                event_id=event_id,
                operation_id=stable_operation_id,
                action="DIRECT_CREATE_MANUAL_STORE_ROUTE",
                mapping_key=route_key,
                old_value="{}",
                new_value=json.dumps(
                    {
                        "scope_id": scope_id,
                        "product_code": target.product_code,
                        "spec_code": target.spec_code,
                        "store": store,
                        "status": "active",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_proposal_id="",
                submitted_by=identity.user_id,
                approved_by=identity.user_id,
                reason=evidence,
                official_revision_before=revision_before,
                official_revision_after=revision_after,
                status="staged",
                created_at=written_at,
            )
        )
        gateway.update_manual_store_route_status(route_key, "active")
        gateway.update_official_repository_metadata(
            {
                "mapping_revision": revision_after,
                "last_published_at": written_at,
                "last_published_by": identity.user_id,
            }
        )
        verified_routes = _manual_store_routes(gateway)
        verify_metadata = gateway.get_official_repository_metadata()
        build_official_mapping_snapshot(
            gateway.list_official_mapping_records(),
            {**verify_metadata, "repository_status": "active"},
            catalog,
            manual_store_route_records=verified_routes,
            item_expansion_records=_item_expansion_records(gateway),
            synced_at=written_at,
        )
        gateway.update_mapping_publish_log_status(event_id, "published")
        gateway.update_official_repository_metadata(
            {"repository_status": "active", "publish_operation_id": ""}
        )
    except Exception as exc:
        return DirectManualStoreRouteAddResult(
            status="failed_closed",
            operation_id=stable_operation_id,
            route_key=route_key,
            revision_before=revision_before,
            revision_after=revision_after,
            product_code=target.product_code,
            spec_code=target.spec_code,
            store=store,
            code="DIRECT_STORE_ROUTE_WRITE_INTERRUPTED",
            reason=(
                "写入中断；正式库保持可核验状态，禁止继续转换。请管理员按"
                f"operation_id={stable_operation_id}核对并恢复：{exc}"
            ),
        )
    return DirectManualStoreRouteAddResult(
        status="created",
        operation_id=stable_operation_id,
        route_key=route_key,
        revision_before=revision_before,
        revision_after=revision_after,
        product_code=target.product_code,
        spec_code=target.spec_code,
        store=store,
    )


def add_confirmed_item_expansion_direct(
    gateway: OwnerMappingPublisherGateway,
    catalog: ProductCatalog,
    *,
    allowed_company_source_pairs: Sequence[tuple[str, str]],
    company_id: str,
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
    target_platform: str,
    components: Sequence[Mapping[str, Any]],
    evidence: str,
    confirmation_token: str,
    operation_id: str | None = None,
    timestamp: str | None = None,
) -> DirectItemExpansionAddResult:
    """Create one confirmed one-to-many rule without replacing existing facts."""

    company_id = clean_identifier(company_id)
    source_profile_id = clean_identifier(source_profile_id)
    identifier_type = clean_identifier(identifier_type)
    source_value = clean_identifier(source_value)
    source_spec = clean_identifier(source_spec)
    target_platform = clean_identifier(target_platform)
    evidence = clean_identifier(evidence)

    def outcome(
        status: str,
        *,
        rule_key: str = "",
        rule_id: str = "",
        revision_before: str = "",
        revision_after: str = "",
        component_count: int = 0,
        code: str = "",
        reason: str = "",
        resolved_operation_id: str | None = None,
    ) -> DirectItemExpansionAddResult:
        return DirectItemExpansionAddResult(
            status=status,
            operation_id=resolved_operation_id or operation_id or "",
            rule_key=rule_key,
            rule_id=rule_id,
            revision_before=revision_before,
            revision_after=revision_after,
            component_count=component_count,
            code=code,
            reason=reason,
        )

    if confirmation_token != DIRECT_ITEM_EXPANSION_CONFIRMATION_TOKEN:
        return outcome(
            "needs_confirmation",
            code="DIRECT_ITEM_EXPANSION_CONFIRMATION_REQUIRED",
            reason="新增正式组合规则前必须取得用户明确的‘以后沿用’确认。",
        )
    identity = gateway.get_current_identity()
    if identity.identity_type != "user" or not clean_identifier(identity.user_id):
        return outcome(
            "blocked",
            code="DIRECT_ITEM_EXPANSION_USER_IDENTITY_REQUIRED",
            reason="必须使用可追溯的飞书用户身份，禁止共享机器人身份写正式组合规则。",
        )
    allowed_pairs = {
        (clean_identifier(company), clean_identifier(profile))
        for company, profile in allowed_company_source_pairs
    }
    if (company_id, source_profile_id) not in allowed_pairs:
        return outcome(
            "needs_admin",
            code="DIRECT_ITEM_EXPANSION_SOURCE_NOT_REGISTERED",
            reason="公司与来源配置的归属关系未登记，不能通过组合规则入口新增。",
        )
    if not evidence:
        return outcome(
            "blocked",
            code="DIRECT_ITEM_EXPANSION_EVIDENCE_REQUIRED",
            reason="必须记录用户确认组成和以后沿用的依据。",
        )
    try:
        rule_key = build_item_expansion_rule_key(
            company_id=company_id,
            source_profile_id=source_profile_id,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
            target_platform=target_platform,
        )
    except ItemExpansionRegistryError as exc:
        return outcome(
            "blocked",
            code="DIRECT_ITEM_EXPANSION_KEY_INVALID",
            reason=str(exc),
        )
    if len(components) < 2:
        return outcome(
            "blocked",
            rule_key=rule_key,
            code="DIRECT_ITEM_EXPANSION_COMPONENTS_INSUFFICIENT",
            reason="组合规则至少需要两个目标商品。",
        )

    canonical_components: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, component in enumerate(components, start=1):
        target = catalog.resolve_unique_target(
            component.get("product_code"), component.get("spec_code")
        )
        try:
            multiplier = float(component.get("quantity_multiplier"))
        except (TypeError, ValueError):
            multiplier = 0
        if target is None or multiplier <= 0:
            return outcome(
                "blocked",
                rule_key=rule_key,
                code="DIRECT_ITEM_EXPANSION_COMPONENT_INVALID",
                reason=f"第{index}个目标商品不存在、不唯一或数量倍数无效。",
            )
        pair = (target.product_code, target.spec_code)
        if pair in seen_targets:
            return outcome(
                "blocked",
                rule_key=rule_key,
                code="DIRECT_ITEM_EXPANSION_COMPONENT_DUPLICATE",
                reason="组合规则包含重复目标商品，请先合并数量倍数。",
            )
        seen_targets.add(pair)
        canonical_components.append(
            {
                "component_index": index,
                "product_code": target.product_code,
                "spec_code": target.spec_code,
                "quantity_multiplier": multiplier,
            }
        )

    metadata = gateway.get_official_repository_metadata()
    revision_before = clean_identifier(metadata.get("mapping_revision"))
    if clean_identifier(metadata.get("repository_status")) != "active":
        return outcome(
            "blocked",
            rule_key=rule_key,
            revision_before=revision_before,
            code="OFFICIAL_REPOSITORY_NOT_ACTIVE",
            reason="正式映射库不是active，需管理员先核对并恢复。",
        )
    revision_after = _next_revision(revision_before)
    existing_rows = _item_expansion_records(gateway)
    same_key_rows = [
        row
        for row in existing_rows
        if clean_identifier(row.get("rule_key")) == rule_key
    ]
    if same_key_rows:
        try:
            existing_rules = normalize_item_expansion_rules(
                existing_rows,
                catalog=catalog,
                repository_revision=revision_before,
            )
        except ItemExpansionRegistryError as exc:
            return outcome(
                "needs_admin",
                rule_key=rule_key,
                revision_before=revision_before,
                revision_after=revision_before,
                code="DIRECT_ITEM_EXPANSION_REGISTRY_INVALID",
                reason=str(exc),
            )
        matching = [rule for rule in existing_rules if rule["rule_key"] == rule_key]
        if len(matching) == 1:
            existing_components = [
                {
                    "component_index": component["component_index"],
                    "product_code": component["product_code"],
                    "spec_code": component["spec_code"],
                    "quantity_multiplier": float(component["quantity_multiplier"]),
                }
                for component in matching[0]["components"]
            ]
            if existing_components == canonical_components:
                return outcome(
                    "already_exists",
                    rule_key=rule_key,
                    rule_id=matching[0]["rule_id"],
                    revision_before=revision_before,
                    revision_after=revision_before,
                    component_count=len(canonical_components),
                    reason="正式库已存在完全相同组合规则，未重复写入。",
                )
        return outcome(
            "needs_admin",
            rule_key=rule_key,
            revision_before=revision_before,
            revision_after=revision_before,
            component_count=len(canonical_components),
            code="DIRECT_ITEM_EXPANSION_CONFLICTS_WITH_OFFICIAL",
            reason="同一来源组合键已存在不同组成或异常状态，禁止普通用户覆盖。",
        )

    written_at = timestamp or _now()
    rule_id = _stable_id("item-expansion-rule", rule_key)
    stable_operation_id = operation_id or _stable_id(
        "direct-item-expansion",
        revision_before,
        rule_key,
        json.dumps(canonical_components, sort_keys=True, separators=(",", ":")),
        identity.user_id,
    )
    records = tuple(
        ItemExpansionComponentRecord(
            component_id=_stable_id(
                "item-expansion-component",
                rule_key,
                str(component["component_index"]),
            ),
            rule_id=rule_id,
            rule_key=rule_key,
            company_id=company_id,
            source_profile_id=source_profile_id,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
            target_platform=target_platform,
            component_index=str(component["component_index"]),
            product_code=component["product_code"],
            spec_code=component["spec_code"],
            quantity_multiplier=(
                str(int(component["quantity_multiplier"]))
                if float(component["quantity_multiplier"]).is_integer()
                else format(float(component["quantity_multiplier"]), ".15g")
            ),
            status="active",
            rule_version=revision_after,
            confirmed_by=identity.user_id,
            confirmed_at=written_at,
            evidence=evidence,
        )
        for component in canonical_components
    )
    official_records = gateway.list_official_mapping_records()
    manual_routes = _manual_store_routes(gateway)
    try:
        build_official_mapping_snapshot(
            official_records,
            {
                **metadata,
                "repository_status": "active",
                "mapping_revision": revision_after,
            },
            catalog,
            manual_store_route_records=manual_routes,
            item_expansion_records=[
                *existing_rows,
                *(record.to_dict() for record in records),
            ],
            synced_at=written_at,
        )
    except OfficialMappingSyncError as exc:
        return outcome(
            "blocked",
            rule_key=rule_key,
            rule_id=rule_id,
            revision_before=revision_before,
            revision_after=revision_after,
            component_count=len(records),
            code="DIRECT_ITEM_EXPANSION_SNAPSHOT_INVALID",
            reason=str(exc),
            resolved_operation_id=stable_operation_id,
        )
    try:
        latest = gateway.get_official_repository_metadata()
        if (
            clean_identifier(latest.get("repository_status")) != "active"
            or clean_identifier(latest.get("mapping_revision")) != revision_before
        ):
            return outcome(
                "blocked",
                rule_key=rule_key,
                rule_id=rule_id,
                revision_before=revision_before,
                revision_after=revision_after,
                component_count=len(records),
                code="DIRECT_ITEM_EXPANSION_REVISION_CHANGED",
                reason="正式映射库在预检后发生变化，未写入。",
                resolved_operation_id=stable_operation_id,
            )
        gateway.update_official_repository_metadata(
            {
                "repository_status": "publishing",
                "publish_operation_id": stable_operation_id,
            }
        )
        gateway.upsert_item_expansion_component_records(
            tuple(replace(record, status="inactive") for record in records)
        )
        event_id = _stable_id("event", stable_operation_id, rule_key)
        gateway.upsert_mapping_publish_log(
            MappingPublishLogRecord(
                event_id=event_id,
                operation_id=stable_operation_id,
                action="DIRECT_CREATE_ITEM_EXPANSION",
                mapping_key=rule_key,
                old_value="{}",
                new_value=json.dumps(
                    {
                        "rule_id": rule_id,
                        "components": canonical_components,
                        "status": "active",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                source_proposal_id="",
                submitted_by=identity.user_id,
                approved_by=identity.user_id,
                reason=evidence,
                official_revision_before=revision_before,
                official_revision_after=revision_after,
                status="staged",
                created_at=written_at,
            )
        )
        gateway.update_item_expansion_rule_status(rule_key, "active")
        gateway.update_official_repository_metadata(
            {
                "mapping_revision": revision_after,
                "last_published_at": written_at,
                "last_published_by": identity.user_id,
            }
        )
        verified_metadata = gateway.get_official_repository_metadata()
        build_official_mapping_snapshot(
            gateway.list_official_mapping_records(),
            {**verified_metadata, "repository_status": "active"},
            catalog,
            manual_store_route_records=_manual_store_routes(gateway),
            item_expansion_records=_item_expansion_records(gateway),
            synced_at=written_at,
        )
        gateway.update_mapping_publish_log_status(event_id, "published")
        gateway.update_official_repository_metadata(
            {"repository_status": "active", "publish_operation_id": ""}
        )
    except Exception as exc:
        return outcome(
            "failed_closed",
            rule_key=rule_key,
            rule_id=rule_id,
            revision_before=revision_before,
            revision_after=revision_after,
            component_count=len(records),
            code="DIRECT_ITEM_EXPANSION_WRITE_INTERRUPTED",
            reason=(
                "写入中断；正式库保持失败关闭状态，请管理员按"
                f"operation_id={stable_operation_id}核对并恢复：{exc}"
            ),
            resolved_operation_id=stable_operation_id,
        )
    return outcome(
        "created",
        rule_key=rule_key,
        rule_id=rule_id,
        revision_before=revision_before,
        revision_after=revision_after,
        component_count=len(records),
        resolved_operation_id=stable_operation_id,
    )


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
        submitted_candidate = _proposal_candidate(proposal)
        resolved_target = catalog.resolve_unique_target(*submitted_candidate)
        candidate = _canonical_proposal_candidate(catalog, proposal)
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
        if resolved_target is None:
            items.append(
                MappingPublishItem(
                    proposal=proposal,
                    record=None,
                    disposition="conflict",
                    reason="候选商品代码与规格代码在管易商品主档中不存在或有多个候选。",
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
            product_code=candidate[0],
            spec_code=candidate[1],
            status="active",
            mapping_version=revision_after,
            confirmed_by=identity.user_id,
            confirmed_at=published_at,
            evidence=proposal.evidence,
            **_canonical_source_fields(
                catalog,
                identifier_type=proposal.identifier_type,
                source_value=proposal.source_value,
                source_spec=proposal.source_spec,
            ),
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
            _canonical_proposal_candidate(catalog, item.proposal)
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
            manual_store_route_records=_manual_store_routes(gateway),
            item_expansion_records=_item_expansion_records(gateway),
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
                            "product_code": item.record.product_code,
                            "spec_code": item.record.spec_code,
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
                manual_store_route_records=_manual_store_routes(gateway),
                item_expansion_records=_item_expansion_records(gateway),
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
