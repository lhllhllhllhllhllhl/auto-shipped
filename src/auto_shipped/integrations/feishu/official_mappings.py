from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from auto_shipped.catalog import ProductCatalog, clean_identifier

from .pending_mappings import build_mapping_key
from .manual_store_routes import (
    ManualStoreRouteError,
    normalize_manual_store_routes,
)
from .item_expansion_registry import (
    ItemExpansionRegistryError,
    normalize_item_expansion_rules,
)
from .mapping_contract import MappingScopeError, normalize_mapping_scope
from .versioned_repository import (
    READABLE_REPOSITORY_STATUSES,
    VersionedRepositoryError,
    record_is_visible,
)


ALLOWED_IDENTIFIER_TYPES = {"product_code", "barcode", "product_name"}
OFFICIAL_MAPPING_SYNC_RECEIPT_SCHEMA_VERSION = (
    "official-mapping-sync-receipt/1.0"
)
OFFICIAL_MAPPING_SYNC_RECEIPT_MAX_AGE_SECONDS = 600
ALLOWED_SYNC_PROVIDERS = {"agent_native_feishu", "lark_cli"}
ALLOWED_READ_CONSISTENCY_MODES = {
    "agent_native_metadata_double_read",
    "lark_cli_metadata_double_read",
}


class OfficialMappingSyncError(ValueError):
    pass


def _canonical_source_fields(
    catalog: ProductCatalog,
    source: Mapping[str, Any],
    *,
    mapping_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
) -> dict[str, str]:
    stored = {
        "brand": clean_identifier(source.get("canonical_source_brand")),
        "product": clean_identifier(source.get("canonical_source_product")),
        "spec": clean_identifier(source.get("canonical_source_spec")),
        "schema_version": clean_identifier(source.get("canonical_schema_version")),
    }
    if identifier_type != "product_name":
        if any(stored.values()):
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}是代码/条码映射，规范名称字段必须留空。"
            )
        return {
            "canonical_source_brand": "",
            "canonical_source_product": "",
            "canonical_source_spec": "",
            "canonical_schema_version": "",
        }

    derived = catalog.canonicalize_source_identity(
        source_product_name=source_value,
        source_spec=source_spec,
    )
    if not derived.product or any(
        issue.endswith("_ambiguous") for issue in derived.issues
    ):
        raise OfficialMappingSyncError(
            f"正式映射{mapping_id}无法生成唯一规范商品主体。"
        )
    if stored["product"]:
        stored_identity = catalog.canonicalize_source_identity(
            source_brand=stored["brand"],
            source_product_name=stored["product"],
            source_spec=stored["spec"],
        )
        if stored_identity.signature != derived.signature:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}的规范字段与来源名称/规格不一致。"
            )
    return {
        "canonical_source_brand": derived.brand,
        "canonical_source_product": derived.product,
        "canonical_source_spec": derived.spec,
        "canonical_schema_version": derived.schema_version,
    }


def build_official_mapping_snapshot(
    records: Iterable[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    catalog: ProductCatalog,
    *,
    manual_store_route_records: Iterable[Mapping[str, Any]] = (),
    item_expansion_records: Iterable[Mapping[str, Any]] = (),
    synced_at: str | None = None,
) -> dict[str, Any]:
    repository_status = clean_identifier(metadata.get("repository_status"))
    if repository_status not in READABLE_REPOSITORY_STATUSES:
        raise OfficialMappingSyncError("正式映射库没有可读取的active版本。")
    revision = clean_identifier(metadata.get("mapping_revision"))
    if not revision:
        raise OfficialMappingSyncError("正式映射库缺少mapping_revision。")

    mappings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for source in records:
        status = clean_identifier(source.get("status"))
        if status == "inactive":
            continue
        mapping_id = clean_identifier(source.get("mapping_id"))
        company_id = clean_identifier(source.get("company_id"))
        source_profile_id = clean_identifier(source.get("source_profile_id"))
        identifier_type = clean_identifier(source.get("identifier_type"))
        source_value = clean_identifier(source.get("source_value"))
        source_spec = clean_identifier(source.get("source_spec"))
        target_platform = clean_identifier(source.get("target_platform"))
        product_code = clean_identifier(source.get("product_code"))
        spec_code = clean_identifier(source.get("spec_code"))
        mapping_version = clean_identifier(source.get("mapping_version"))
        try:
            if not record_is_visible(
                status=status,
                record_version=mapping_version,
                repository_revision=revision,
                record_label=f"正式映射{mapping_id or '<unknown>'}",
            ):
                continue
        except VersionedRepositoryError as exc:
            raise OfficialMappingSyncError(str(exc)) from exc
        required = {
            "mapping_id": mapping_id,
            "company_id": company_id,
            "source_profile_id": source_profile_id,
            "identifier_type": identifier_type,
            "target_platform": target_platform,
            "product_code": product_code,
            "mapping_version": mapping_version,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id or '<unknown>'}缺少字段：{', '.join(missing)}"
            )
        if not source_value and not source_spec:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}的来源商品标识和来源规格不能同时为空。"
            )
        if identifier_type not in ALLOWED_IDENTIFIER_TYPES:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}使用未知identifier_type：{identifier_type}"
            )
        try:
            mapping_scope = normalize_mapping_scope(
                company_id=company_id,
                source_profile_id=source_profile_id,
                identifier_type=identifier_type,
                source_value=source_value,
            )
        except MappingScopeError as exc:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}作用范围无效：{exc}"
            ) from exc
        if target_platform != "guanyi":
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}目标平台不是guanyi。"
            )
        target = catalog.resolve_unique_target(product_code, spec_code)
        if target is None:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}的管易商品/规格组合不存在或存在多个候选。"
            )
        product_code = target.product_code
        spec_code = target.spec_code
        expected_key = build_mapping_key(
            company_id=company_id,
            source_profile_id=source_profile_id,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
            target_platform=target_platform,
        )
        actual_key = clean_identifier(source.get("mapping_key"))
        if actual_key != expected_key:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}的mapping_key校验失败。"
            )
        if mapping_id in seen_ids:
            raise OfficialMappingSyncError(f"正式映射mapping_id重复：{mapping_id}")
        if actual_key in seen_keys:
            raise OfficialMappingSyncError(f"正式映射mapping_key重复：{actual_key}")
        seen_ids.add(mapping_id)
        seen_keys.add(actual_key)
        mapping = {
            "mapping_id": mapping_id,
            "mapping_key": actual_key,
            "company_id": company_id,
            "source_profile_id": source_profile_id,
            "identifier_type": identifier_type,
            "source_value": source_value,
            "source_spec": source_spec,
            "mapping_scope": mapping_scope,
            "product_code": product_code,
            "spec_code": spec_code,
            "confirmed": True,
            "confirmed_by": clean_identifier(source.get("confirmed_by")),
            "confirmed_at": clean_identifier(source.get("confirmed_at")),
            "reason": clean_identifier(source.get("evidence")) or "飞书正式映射库",
            "mapping_version": mapping_version,
            "reuse_scope": "future",
            "mapping_authority": "feishu_official",
            **_canonical_source_fields(
                catalog,
                source,
                mapping_id=mapping_id,
                identifier_type=identifier_type,
                source_value=source_value,
                source_spec=source_spec,
            ),
        }
        mappings.append(mapping)

    mappings.sort(key=lambda item: item["mapping_key"])
    try:
        manual_store_routes = normalize_manual_store_routes(
            manual_store_route_records,
            catalog=catalog,
            repository_revision=revision,
        )
    except ManualStoreRouteError as exc:
        raise OfficialMappingSyncError(str(exc)) from exc
    try:
        item_expansions = normalize_item_expansion_rules(
            item_expansion_records,
            catalog=catalog,
            repository_revision=revision,
        )
    except ItemExpansionRegistryError as exc:
        raise OfficialMappingSyncError(str(exc)) from exc
    timestamp = synced_at or datetime.now(timezone.utc).isoformat()
    snapshot: dict[str, Any] = {
        "schema_version": "2.0",
        "source": "feishu_official_mapping_repository",
        "mapping_revision": revision,
        "repository_status": "active",
        "canonical_schema_version": catalog.phrase_normalizer.schema_version,
        "synced_at": timestamp,
        "mappings": mappings,
        "manual_store_routes": manual_store_routes,
        "item_expansions": item_expansions,
    }
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    snapshot["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()
    return snapshot


def validate_official_mapping_snapshot(
    snapshot: Mapping[str, Any],
    catalog: ProductCatalog,
) -> dict[str, Any]:
    """Validate a cached/native snapshot before it can participate in conversion."""

    document = dict(snapshot)
    if clean_identifier(document.get("schema_version")) != "2.0":
        raise OfficialMappingSyncError("正式映射快照schema_version不是2.0。")
    if clean_identifier(document.get("source")) != "feishu_official_mapping_repository":
        raise OfficialMappingSyncError("正式映射快照来源标识不正确。")
    if clean_identifier(document.get("repository_status")) != "active":
        raise OfficialMappingSyncError("正式映射快照状态不是active。")
    revision = clean_identifier(document.get("mapping_revision"))
    if not revision:
        raise OfficialMappingSyncError("正式映射快照缺少mapping_revision。")
    canonical_schema_version = clean_identifier(
        document.get("canonical_schema_version")
    )
    if canonical_schema_version and (
        canonical_schema_version != catalog.phrase_normalizer.schema_version
    ):
        raise OfficialMappingSyncError("正式映射快照规范商品Schema版本不受支持。")
    records = document.get("mappings")
    if not isinstance(records, list):
        raise OfficialMappingSyncError("正式映射快照mappings不是列表。")
    manual_store_routes = document.get("manual_store_routes", [])
    if not isinstance(manual_store_routes, list):
        raise OfficialMappingSyncError("正式映射快照manual_store_routes不是列表。")
    item_expansions = document.get("item_expansions", [])
    if not isinstance(item_expansions, list):
        raise OfficialMappingSyncError("正式映射快照item_expansions不是列表。")

    expected_digest = clean_identifier(document.get("snapshot_sha256"))
    canonical_document = dict(document)
    canonical_document.pop("snapshot_sha256", None)
    canonical = json.dumps(
        canonical_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual_digest = hashlib.sha256(canonical).hexdigest()
    if not expected_digest or expected_digest != actual_digest:
        raise OfficialMappingSyncError("正式映射快照SHA-256校验失败。")

    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for source in records:
        if not isinstance(source, Mapping):
            raise OfficialMappingSyncError("正式映射快照包含非对象记录。")
        mapping_id = clean_identifier(source.get("mapping_id"))
        company_id = clean_identifier(source.get("company_id"))
        source_profile_id = clean_identifier(source.get("source_profile_id"))
        identifier_type = clean_identifier(source.get("identifier_type"))
        source_value = clean_identifier(source.get("source_value"))
        source_spec = clean_identifier(source.get("source_spec"))
        product_code = clean_identifier(source.get("product_code"))
        spec_code = clean_identifier(source.get("spec_code"))
        mapping_version = clean_identifier(source.get("mapping_version"))
        canonical_fields = _canonical_source_fields(
            catalog,
            source,
            mapping_id=mapping_id,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
        )
        required = {
            "mapping_id": mapping_id,
            "company_id": company_id,
            "source_profile_id": source_profile_id,
            "identifier_type": identifier_type,
            "product_code": product_code,
            "mapping_version": mapping_version,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id or '<unknown>'}缺少字段：{', '.join(missing)}"
            )
        if not source_value and not source_spec:
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}的来源商品标识和来源规格不能同时为空。"
            )
        if identifier_type not in ALLOWED_IDENTIFIER_TYPES:
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}使用未知identifier_type。"
            )
        try:
            mapping_scope = normalize_mapping_scope(
                company_id=company_id,
                source_profile_id=source_profile_id,
                identifier_type=identifier_type,
                source_value=source_value,
            )
        except MappingScopeError as exc:
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}作用范围无效：{exc}"
            ) from exc
        stored_mapping_scope = clean_identifier(source.get("mapping_scope"))
        # Older signed snapshots predate the explicit mapping_scope field.
        # Their scope is still derivable from the existing wildcard contract.
        # New snapshots always persist the field; reject only an explicit
        # conflict so a temporary Feishu outage does not invalidate a valid
        # pre-upgrade cache.
        if stored_mapping_scope and stored_mapping_scope != mapping_scope:
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}的mapping_scope与公司/来源作用域不一致。"
            )
        if source.get("confirmed") is not True:
            raise OfficialMappingSyncError(f"正式映射快照记录{mapping_id}未确认。")
        if clean_identifier(source.get("reuse_scope")) != "future":
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}缺少长期复用授权标记。"
            )
        if clean_identifier(source.get("mapping_authority")) != "feishu_official":
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}缺少正式库权威来源标记。"
            )
        target = catalog.resolve_unique_target(product_code, spec_code)
        if target is None:
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}的管易商品/规格组合不存在或存在多个候选。"
            )
        if (target.product_code, target.spec_code) != (product_code, spec_code):
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}没有保存规范化后的管易商品/规格组合。"
            )
        expected_key = build_mapping_key(
            company_id=company_id,
            source_profile_id=source_profile_id,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
            target_platform="guanyi",
        )
        actual_key = clean_identifier(source.get("mapping_key"))
        if actual_key != expected_key:
            raise OfficialMappingSyncError(
                f"正式映射快照记录{mapping_id}的mapping_key校验失败。"
            )
        if mapping_id in seen_ids:
            raise OfficialMappingSyncError(f"正式映射快照mapping_id重复：{mapping_id}")
        if actual_key in seen_keys:
            raise OfficialMappingSyncError(f"正式映射快照mapping_key重复：{actual_key}")
        seen_ids.add(mapping_id)
        seen_keys.add(actual_key)
        if revision.isdigit() and mapping_version.isdigit():
            if int(mapping_version) > int(revision):
                raise OfficialMappingSyncError(
                    f"正式映射快照记录{mapping_id}版本高于库版本。"
                )
        for field, expected in canonical_fields.items():
            if canonical_schema_version and clean_identifier(source.get(field)) != expected:
                raise OfficialMappingSyncError(
                    f"正式映射快照记录{mapping_id}的{field}未规范化。"
                )
    try:
        normalized_routes = normalize_manual_store_routes(
            manual_store_routes,
            catalog=catalog,
            repository_revision=revision,
        )
    except ManualStoreRouteError as exc:
        raise OfficialMappingSyncError(str(exc)) from exc
    if normalized_routes != manual_store_routes:
        raise OfficialMappingSyncError("正式映射快照手工单店铺关联未规范化。")
    try:
        component_rows = []
        for rule in item_expansions:
            if not isinstance(rule, Mapping):
                raise ItemExpansionRegistryError("正式映射快照包含非对象组合规则。")
            for component in rule.get("components") or []:
                component_rows.append(
                    {
                        "component_id": (
                            f"{clean_identifier(rule.get('rule_id'))}-"
                            f"{clean_identifier(component.get('component_index'))}"
                        ),
                        "rule_id": rule.get("rule_id"),
                        "rule_key": rule.get("rule_key"),
                        "company_id": rule.get("company_id"),
                        "source_profile_id": rule.get("source_profile_id"),
                        "identifier_type": rule.get("identifier_type"),
                        "source_value": rule.get("source_value"),
                        "source_spec": rule.get("source_spec"),
                        "target_platform": rule.get("target_platform"),
                        "component_index": component.get("component_index"),
                        "product_code": component.get("product_code"),
                        "spec_code": component.get("spec_code"),
                        "quantity_multiplier": component.get("quantity_multiplier"),
                        "status": rule.get("status"),
                        "rule_version": rule.get("rule_version"),
                        "confirmed_by": rule.get("confirmed_by"),
                        "confirmed_at": rule.get("confirmed_at"),
                        "evidence": rule.get("evidence"),
                    }
                )
        normalized_expansions = normalize_item_expansion_rules(
            component_rows,
            catalog=catalog,
            repository_revision=revision,
        )
    except ItemExpansionRegistryError as exc:
        raise OfficialMappingSyncError(str(exc)) from exc
    if normalized_expansions != item_expansions:
        raise OfficialMappingSyncError("正式映射快照组合商品规则未规范化。")
    return document


def load_official_mapping_snapshot(
    path: str | Path,
    catalog: ProductCatalog,
) -> dict[str, Any]:
    try:
        snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfficialMappingSyncError(f"无法读取正式映射快照：{exc}") from exc
    if not isinstance(snapshot, dict):
        raise OfficialMappingSyncError("正式映射快照根节点不是对象。")
    return validate_official_mapping_snapshot(snapshot, catalog)


def write_official_mapping_snapshot(
    path: str | Path,
    snapshot: Mapping[str, Any],
) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(snapshot), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_utc_timestamp(value: Any, *, field_name: str) -> datetime:
    text = clean_identifier(value)
    if not text:
        raise OfficialMappingSyncError(f"正式映射同步凭证缺少{field_name}。")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfficialMappingSyncError(
            f"正式映射同步凭证{field_name}不是有效时间。"
        ) from exc
    if parsed.tzinfo is None:
        raise OfficialMappingSyncError(
            f"正式映射同步凭证{field_name}必须包含时区。"
        )
    return parsed.astimezone(timezone.utc)


def write_official_mapping_sync_receipt(
    path: str | Path,
    *,
    snapshot_path: str | Path,
    snapshot: Mapping[str, Any],
    provider: str,
    read_consistency: str,
    read_attempts: int,
    issued_at: str | None = None,
) -> Path:
    """Sign a short-lived proof that one task performed a fresh, stable read."""

    normalized_provider = clean_identifier(provider)
    if normalized_provider not in ALLOWED_SYNC_PROVIDERS:
        raise OfficialMappingSyncError("正式映射同步提供器未登记。")
    normalized_consistency = clean_identifier(read_consistency)
    if normalized_consistency not in ALLOWED_READ_CONSISTENCY_MODES:
        raise OfficialMappingSyncError("正式映射同步缺少版本前后双读证据。")
    if (
        isinstance(read_attempts, bool)
        or not isinstance(read_attempts, int)
        or read_attempts < 1
        or read_attempts > 3
    ):
        raise OfficialMappingSyncError("正式映射同步读取次数无效。")

    snapshot_file = Path(snapshot_path).expanduser().resolve()
    if not snapshot_file.is_file():
        raise OfficialMappingSyncError("正式映射快照不存在，不能签发同步凭证。")
    timestamp = issued_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": OFFICIAL_MAPPING_SYNC_RECEIPT_SCHEMA_VERSION,
        "status": "synced",
        "receipt_id": uuid.uuid4().hex,
        "provider": normalized_provider,
        "issued_at": timestamp,
        "snapshot": {
            "file_name": snapshot_file.name,
            "file_sha256": _file_sha256(snapshot_file),
            "snapshot_sha256": clean_identifier(snapshot.get("snapshot_sha256")),
            "mapping_revision": clean_identifier(snapshot.get("mapping_revision")),
            "synced_at": clean_identifier(snapshot.get("synced_at")),
        },
        "read_consistency": {
            "mode": normalized_consistency,
            "attempts": read_attempts,
        },
        "privacy": "no_order_or_recipient_data",
    }
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def validate_official_mapping_sync_receipt(
    receipt_path: str | Path,
    snapshot_path: str | Path,
    catalog: ProductCatalog,
    *,
    max_age_seconds: int = OFFICIAL_MAPPING_SYNC_RECEIPT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate freshness and bind a receipt to the exact snapshot bytes."""

    receipt_file = Path(receipt_path).expanduser().resolve()
    snapshot_file = Path(snapshot_path).expanduser().resolve()
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfficialMappingSyncError(
            f"无法读取正式映射同步凭证：{exc}"
        ) from exc
    if not isinstance(receipt, dict):
        raise OfficialMappingSyncError("正式映射同步凭证根节点不是对象。")
    if (
        clean_identifier(receipt.get("schema_version"))
        != OFFICIAL_MAPPING_SYNC_RECEIPT_SCHEMA_VERSION
        or clean_identifier(receipt.get("status")) != "synced"
        or clean_identifier(receipt.get("privacy"))
        != "no_order_or_recipient_data"
    ):
        raise OfficialMappingSyncError("正式映射同步凭证合同或状态无效。")
    receipt_id = clean_identifier(receipt.get("receipt_id"))
    if len(receipt_id) != 32 or any(
        character not in "0123456789abcdef" for character in receipt_id
    ):
        raise OfficialMappingSyncError("正式映射同步凭证ID无效。")
    provider = clean_identifier(receipt.get("provider"))
    if provider not in ALLOWED_SYNC_PROVIDERS:
        raise OfficialMappingSyncError("正式映射同步凭证提供器未登记。")
    consistency = receipt.get("read_consistency")
    if not isinstance(consistency, dict):
        raise OfficialMappingSyncError("正式映射同步凭证缺少双读一致性证据。")
    consistency_mode = clean_identifier(consistency.get("mode"))
    if consistency_mode not in ALLOWED_READ_CONSISTENCY_MODES:
        raise OfficialMappingSyncError("正式映射同步凭证未完成版本前后双读。")
    attempts = consistency.get("attempts")
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or attempts < 1
        or attempts > 3
    ):
        raise OfficialMappingSyncError("正式映射同步凭证读取次数无效。")

    snapshot = load_official_mapping_snapshot(snapshot_file, catalog)
    bound = receipt.get("snapshot")
    if not isinstance(bound, dict):
        raise OfficialMappingSyncError("正式映射同步凭证缺少快照绑定。")
    expected = {
        "file_sha256": _file_sha256(snapshot_file),
        "snapshot_sha256": clean_identifier(snapshot.get("snapshot_sha256")),
        "mapping_revision": clean_identifier(snapshot.get("mapping_revision")),
        "synced_at": clean_identifier(snapshot.get("synced_at")),
    }
    for field_name, expected_value in expected.items():
        if clean_identifier(bound.get(field_name)) != expected_value:
            raise OfficialMappingSyncError(
                f"正式映射同步凭证与当前快照的{field_name}不一致。"
            )

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = _parse_utc_timestamp(receipt.get("issued_at"), field_name="issued_at")
    synced_at = _parse_utc_timestamp(snapshot.get("synced_at"), field_name="synced_at")
    for field_name, timestamp in (("issued_at", issued_at), ("synced_at", synced_at)):
        age = (current - timestamp).total_seconds()
        if age < -60:
            raise OfficialMappingSyncError(
                f"正式映射同步凭证{field_name}晚于当前时间。"
            )
        if age > max_age_seconds:
            raise OfficialMappingSyncError(
                "正式映射同步凭证已过期；必须为当前转换任务重新读取正式库。"
            )

    return {
        "status": "ready",
        "provider": provider,
        "mapping_revision": expected["mapping_revision"],
        "synced_at": expected["synced_at"],
        "snapshot_sha256": expected["snapshot_sha256"],
        "receipt_id": receipt_id,
        "receipt_issued_at": clean_identifier(receipt.get("issued_at")),
        "read_consistency": consistency_mode,
        "read_attempts": attempts,
    }
