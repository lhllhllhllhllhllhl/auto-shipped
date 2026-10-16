from __future__ import annotations

import hashlib
import json
import os
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


ALLOWED_IDENTIFIER_TYPES = {"product_code", "barcode", "product_name"}


class OfficialMappingSyncError(ValueError):
    pass


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
    if repository_status != "active":
        raise OfficialMappingSyncError("正式映射库状态不是active，拒绝生成快照。")
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
        if status != "active":
            raise OfficialMappingSyncError(
                f"映射{source.get('mapping_id')!r}状态不是active/inactive。"
            )
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
        if revision.isdigit() and mapping_version.isdigit():
            if int(mapping_version) > int(revision):
                raise OfficialMappingSyncError(
                    f"正式映射{mapping_id}版本高于库版本。"
                )
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
        "repository_status": repository_status,
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
