from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from auto_shipped.catalog import ProductCatalog, clean_identifier

from .owner_publisher import MappingPublishLogRecord
from .pending_mappings import FeishuIdentity


CANONICAL_MAPPING_MIGRATION_CONFIRMATION = (
    "CONFIRM_MIGRATE_CANONICAL_MAPPING_SCHEMA"
)


class CanonicalMappingMigrationError(ValueError):
    pass


class CanonicalMappingMigrationGateway(Protocol):
    def get_current_identity(self) -> FeishuIdentity: ...

    def get_official_repository_metadata(self) -> dict[str, str]: ...

    def update_official_repository_metadata(self, updates: dict[str, str]) -> None: ...

    def list_official_mapping_records(self) -> list[dict[str, str]]: ...

    def official_mapping_schema_version(self) -> str: ...

    def migrate_official_mapping_canonical_fields(
        self, updates: Sequence[Mapping[str, str]]
    ) -> None: ...

    def upsert_mapping_publish_log(self, record: MappingPublishLogRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class CanonicalMappingMigrationPlan:
    status: str
    mapping_revision: str
    sheet_schema_before: str
    canonical_schema_version: str
    records: tuple[dict[str, str], ...]
    conflicts: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "records": list(self.records),
            "conflicts": list(self.conflicts),
        }


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"


def plan_canonical_mapping_migration(
    records: Sequence[Mapping[str, Any]],
    catalog: ProductCatalog,
    *,
    mapping_revision: str,
    sheet_schema_before: str,
) -> CanonicalMappingMigrationPlan:
    planned: list[dict[str, str]] = []
    targets_by_identity: dict[
        tuple[str, str, str, str, str, str], set[tuple[str, str]]
    ] = {}
    mapping_ids_by_identity: dict[
        tuple[str, str, str, str, str, str], list[str]
    ] = {}
    for source in records:
        mapping_id = clean_identifier(source.get("mapping_id"))
        identifier_type = clean_identifier(source.get("identifier_type"))
        fields = {
            "mapping_id": mapping_id,
            "canonical_source_brand": "",
            "canonical_source_product": "",
            "canonical_source_spec": "",
            "canonical_schema_version": "",
        }
        if identifier_type == "product_name":
            identity = catalog.canonicalize_source_identity(
                source_product_name=source.get("source_value"),
                source_spec=source.get("source_spec"),
            )
            if not identity.product or any(
                issue.endswith("_ambiguous") for issue in identity.issues
            ):
                raise CanonicalMappingMigrationError(
                    f"正式映射{mapping_id}无法生成唯一规范商品主体。"
                )
            fields.update(
                {
                    "canonical_source_brand": identity.brand,
                    "canonical_source_product": identity.product,
                    "canonical_source_spec": identity.spec,
                    "canonical_schema_version": identity.schema_version,
                }
            )
            identity_key = (
                clean_identifier(source.get("company_id")),
                clean_identifier(source.get("source_profile_id")),
                identity.schema_version,
                identity.brand,
                identity.product,
                identity.spec,
            )
            targets_by_identity.setdefault(identity_key, set()).add(
                (
                    clean_identifier(source.get("product_code")),
                    clean_identifier(source.get("spec_code")),
                )
            )
            mapping_ids_by_identity.setdefault(identity_key, []).append(mapping_id)
        planned.append(fields)

    conflicts = tuple(
        {
            "canonical_identity": {
                "company_id": key[0],
                "source_profile_id": key[1],
                "schema_version": key[2],
                "brand": key[3],
                "product": key[4],
                "spec": key[5],
            },
            "mapping_ids": sorted(mapping_ids_by_identity[key]),
            "target_count": len(targets),
        }
        for key, targets in targets_by_identity.items()
        if len(targets) > 1
    )
    return CanonicalMappingMigrationPlan(
        status="blocked" if conflicts else "ready",
        mapping_revision=clean_identifier(mapping_revision),
        sheet_schema_before=sheet_schema_before,
        canonical_schema_version=catalog.phrase_normalizer.schema_version,
        records=tuple(planned),
        conflicts=conflicts,
    )


def migrate_canonical_mapping_schema(
    gateway: CanonicalMappingMigrationGateway,
    catalog: ProductCatalog,
    *,
    allowed_owner_user_ids: Sequence[str],
    confirmation_token: str,
    operation_id: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    if confirmation_token != CANONICAL_MAPPING_MIGRATION_CONFIRMATION:
        return {
            "status": "needs_confirmation",
            "code": "CANONICAL_MAPPING_MIGRATION_CONFIRMATION_REQUIRED",
        }
    identity = gateway.get_current_identity()
    allowed = {clean_identifier(value) for value in allowed_owner_user_ids}
    if identity.identity_type != "user" or identity.user_id not in allowed:
        return {
            "status": "blocked",
            "code": "CANONICAL_MAPPING_MIGRATION_OWNER_REQUIRED",
        }
    metadata = gateway.get_official_repository_metadata()
    revision_before = clean_identifier(metadata.get("mapping_revision"))
    if clean_identifier(metadata.get("repository_status")) != "active":
        return {
            "status": "blocked",
            "code": "OFFICIAL_REPOSITORY_NOT_ACTIVE",
            "mapping_revision": revision_before,
        }
    if not revision_before.isdigit():
        raise CanonicalMappingMigrationError("正式映射库版本不是整数。")
    records = gateway.list_official_mapping_records()
    plan = plan_canonical_mapping_migration(
        records,
        catalog,
        mapping_revision=revision_before,
        sheet_schema_before=gateway.official_mapping_schema_version(),
    )
    if plan.status != "ready":
        return plan.to_dict()
    already_current = (
        plan.sheet_schema_before == "2.0"
        and all(
            all(clean_identifier(row.get(key)) == value for key, value in update.items())
            for row, update in zip(records, plan.records, strict=True)
        )
    )
    if already_current:
        return {
            "status": "already_current",
            "mapping_revision": revision_before,
            "canonical_schema_version": plan.canonical_schema_version,
            "record_count": len(plan.records),
        }

    revision_after = str(int(revision_before) + 1)
    migrated_at = timestamp or datetime.now(timezone.utc).isoformat()
    stable_operation_id = operation_id or _stable_id(
        "canonical-migration",
        revision_before,
        plan.canonical_schema_version,
    )
    gateway.update_official_repository_metadata(
        {
            "repository_status": "publishing",
            "publish_operation_id": stable_operation_id,
        }
    )
    gateway.migrate_official_mapping_canonical_fields(plan.records)
    gateway.upsert_mapping_publish_log(
        MappingPublishLogRecord(
            event_id=_stable_id("event", stable_operation_id, "canonical-schema"),
            operation_id=stable_operation_id,
            action="MIGRATE_CANONICAL_SCHEMA",
            mapping_key="*",
            old_value=json.dumps(
                {"sheet_schema_version": plan.sheet_schema_before},
                ensure_ascii=False,
                sort_keys=True,
            ),
            new_value=json.dumps(
                {
                    "sheet_schema_version": "2.0",
                    "canonical_schema_version": plan.canonical_schema_version,
                    "record_count": len(plan.records),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            source_proposal_id="",
            submitted_by=identity.user_id,
            approved_by=identity.user_id,
            reason="为正式商品名称映射补充程序生成的品牌、商品主体和规格规范字段。",
            official_revision_before=revision_before,
            official_revision_after=revision_after,
            status="applied",
            created_at=migrated_at,
        )
    )
    gateway.update_official_repository_metadata(
        {
            "mapping_revision": revision_after,
            "canonical_schema_version": plan.canonical_schema_version,
            "mapping_sheet_schema_version": "2.0",
            "repository_status": "active",
            "publish_operation_id": stable_operation_id,
        }
    )
    return {
        "status": "migrated",
        "operation_id": stable_operation_id,
        "mapping_revision_before": revision_before,
        "mapping_revision_after": revision_after,
        "canonical_schema_version": plan.canonical_schema_version,
        "record_count": len(plan.records),
    }
