from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .product_catalog import ProductCatalog, clean_identifier


PERSIST_MAPPING_CONFIRMATION = "CONFIRM_PERSIST_PRODUCT_MAPPING"
ALLOWED_IDENTIFIER_TYPES = {"product_code", "barcode", "product_name"}


class ProductMappingStoreError(ValueError):
    pass


def _mapping_id(
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
) -> str:
    raw = "\x1f".join(
        [source_profile_id, identifier_type, source_value, source_spec]
    ).encode("utf-8")
    return f"local-{hashlib.sha256(raw).hexdigest()[:20]}"


def _load_overlay(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "2.0", "mappings": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductMappingStoreError(f"无法读取商品映射覆盖层: {exc}") from exc
    if data.get("schema_version") != "2.0" or not isinstance(data.get("mappings"), list):
        raise ProductMappingStoreError("商品映射覆盖层必须使用 schema_version 2.0 和列表格式")
    return data


def save_confirmed_mapping(
    overlay_path: str | Path,
    catalog: ProductCatalog,
    *,
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    product_code: str,
    spec_code: str,
    source_spec: str = "",
    reason: str = "用户确认",
    confirmation_token: str,
) -> Path:
    if confirmation_token != PERSIST_MAPPING_CONFIRMATION:
        raise ProductMappingStoreError("缺少持久保存商品映射的精确确认口令")

    source_profile_id = clean_identifier(source_profile_id)
    identifier_type = clean_identifier(identifier_type)
    source_value = clean_identifier(source_value)
    source_spec = clean_identifier(source_spec)
    product_code = clean_identifier(product_code)
    spec_code = clean_identifier(spec_code)
    if not source_profile_id or not source_value:
        raise ProductMappingStoreError("来源配置和来源商品标识不能为空")
    if identifier_type not in ALLOWED_IDENTIFIER_TYPES:
        raise ProductMappingStoreError(f"不支持的来源商品标识类型: {identifier_type}")
    if not catalog.has_unique_pair(product_code, spec_code):
        raise ProductMappingStoreError("管易商品代码与规格代码在商品主数据中不是唯一组合")

    path = Path(overlay_path).expanduser().resolve()
    document = _load_overlay(path)
    now = datetime.now(timezone.utc).isoformat()
    mapping_id = _mapping_id(
        source_profile_id,
        identifier_type,
        source_value,
        source_spec,
    )
    record = {
        "mapping_id": mapping_id,
        "source_profile_id": source_profile_id,
        "identifier_type": identifier_type,
        "source_value": source_value,
        "product_code": product_code,
        "spec_code": spec_code,
        "confirmed": True,
        "confirmed_at": now,
        "reason": clean_identifier(reason) or "用户确认",
    }
    if source_spec:
        record["source_spec"] = source_spec

    mappings = [
        item
        for item in document["mappings"]
        if str(item.get("mapping_id") or "") != mapping_id
    ]
    mappings.append(record)
    document["mappings"] = mappings
    document["updated_at"] = now

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path
