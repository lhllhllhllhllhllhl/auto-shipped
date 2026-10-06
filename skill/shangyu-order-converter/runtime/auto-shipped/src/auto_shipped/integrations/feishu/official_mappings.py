from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from auto_shipped.catalog import ProductCatalog, clean_identifier

from .pending_mappings import build_mapping_key


ALLOWED_IDENTIFIER_TYPES = {"product_code", "barcode", "product_name"}


class OfficialMappingSyncError(ValueError):
    pass


def build_official_mapping_snapshot(
    records: Iterable[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    catalog: ProductCatalog,
    *,
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
            "source_value": source_value,
            "target_platform": target_platform,
            "product_code": product_code,
            "spec_code": spec_code,
            "mapping_version": mapping_version,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id or '<unknown>'}缺少字段：{', '.join(missing)}"
            )
        if identifier_type not in ALLOWED_IDENTIFIER_TYPES:
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}使用未知identifier_type：{identifier_type}"
            )
        if target_platform != "guanyi":
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}目标平台不是guanyi。"
            )
        if not catalog.has_unique_pair(product_code, spec_code):
            raise OfficialMappingSyncError(
                f"正式映射{mapping_id}的管易商品/规格组合不存在或不唯一。"
            )
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
            "product_code": product_code,
            "spec_code": spec_code,
            "confirmed": True,
            "confirmed_by": clean_identifier(source.get("confirmed_by")),
            "confirmed_at": clean_identifier(source.get("confirmed_at")),
            "reason": clean_identifier(source.get("evidence")) or "飞书正式映射库",
            "mapping_version": mapping_version,
        }
        if source_spec:
            mapping["source_spec"] = source_spec
        mappings.append(mapping)

    mappings.sort(key=lambda item: item["mapping_key"])
    timestamp = synced_at or datetime.now(timezone.utc).isoformat()
    snapshot: dict[str, Any] = {
        "schema_version": "2.0",
        "source": "feishu_official_mapping_repository",
        "mapping_revision": revision,
        "repository_status": repository_status,
        "synced_at": timestamp,
        "mappings": mappings,
    }
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    snapshot["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()
    return snapshot


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

