from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .product_catalog import ProductCatalog


def _clean(value: Any) -> str:
    return str(value or "").replace("\t", "").strip()


BATCH_REPLACEMENT_CONFIRMATION = "CONFIRM_REPLACE_OFFICIAL_MAPPING_FOR_CURRENT_BATCH"
ALLOWED_IDENTIFIER_TYPES = {"product_code", "barcode", "product_name"}


class BatchMappingReplacementError(ValueError):
    """Raised when a current-batch replacement cannot be proven safe."""


_REQUIRED_FIELDS = {
    "replacement_id",
    "company_id",
    "source_profile_id",
    "identifier_type",
    "source_value",
    "source_spec",
    "expected_product_code",
    "expected_spec_code",
    "replacement_product_code",
    "replacement_spec_code",
    "source_fingerprint",
    "scope",
    "confirmed_by",
    "confirmed_at",
    "reason",
}


def validate_batch_mapping_replacement_document(
    document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if (
        document.get("schema_version") != "1.0"
        or document.get("source")
        != "current_batch_official_mapping_replacements"
        or not isinstance(document.get("replacements"), list)
    ):
        raise BatchMappingReplacementError("当前批次映射替换记录格式不正确")
    unexpected_document_fields = set(document) - {
        "schema_version",
        "source",
        "updated_at",
        "replacements",
    }
    if unexpected_document_fields:
        raise BatchMappingReplacementError(
            "当前批次映射替换文件包含未知字段："
            + "、".join(sorted(unexpected_document_fields))
        )
    if "updated_at" in document and not isinstance(document["updated_at"], str):
        raise BatchMappingReplacementError("当前批次映射替换文件updated_at必须是文本")
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_scopes: set[tuple[str, str, str, str, str]] = set()
    for index, raw in enumerate(document["replacements"], start=1):
        if not isinstance(raw, Mapping):
            raise BatchMappingReplacementError(f"第{index}条临时替换不是对象")
        missing = _REQUIRED_FIELDS - set(raw)
        unexpected = set(raw) - _REQUIRED_FIELDS
        if missing or unexpected:
            details = []
            if missing:
                details.append("缺少" + "、".join(sorted(missing)))
            if unexpected:
                details.append("未知" + "、".join(sorted(unexpected)))
            raise BatchMappingReplacementError(
                f"第{index}条临时替换字段不符合Schema：" + "；".join(details)
            )
        non_string = [key for key, value in raw.items() if not isinstance(value, str)]
        if non_string:
            raise BatchMappingReplacementError(
                f"第{index}条临时替换字段必须是文本："
                + "、".join(sorted(non_string))
            )
        record = {key: _clean(value) for key, value in raw.items()}
        required_non_blank = (
            "replacement_id",
            "company_id",
            "source_profile_id",
            "expected_product_code",
            "replacement_product_code",
            "confirmed_by",
            "confirmed_at",
            "reason",
        )
        blank = [name for name in required_non_blank if not record[name]]
        if blank:
            raise BatchMappingReplacementError(
                f"第{index}条临时替换缺少值：" + "、".join(blank)
            )
        if record["identifier_type"] not in ALLOWED_IDENTIFIER_TYPES:
            raise BatchMappingReplacementError(
                f"第{index}条临时替换的identifier_type不受支持"
            )
        if not record["source_value"] and not record["source_spec"]:
            raise BatchMappingReplacementError(
                f"第{index}条临时替换的来源值和来源规格不能同时为空"
            )
        if record["scope"] != "current_batch" or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", record["source_fingerprint"]
        ):
            raise BatchMappingReplacementError(
                f"第{index}条临时替换未正确绑定当前批次SHA-256"
            )
        if record["replacement_id"] in seen_ids:
            raise BatchMappingReplacementError(
                f"临时替换replacement_id重复：{record['replacement_id']}"
            )
        scope_key = (
            record["source_profile_id"],
            record["identifier_type"],
            record["source_value"],
            record["source_spec"],
            record["source_fingerprint"],
        )
        if scope_key in seen_scopes:
            raise BatchMappingReplacementError(
                f"同一来源键和文件指纹存在多条临时替换：{record['replacement_id']}"
            )
        seen_ids.add(record["replacement_id"])
        seen_scopes.add(scope_key)
        validated.append(record)
    return validated


def _replacement_id(
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
    source_fingerprint: str,
) -> str:
    raw = "\x1f".join(
        [
            source_profile_id,
            identifier_type,
            source_value,
            source_spec,
            source_fingerprint,
        ]
    ).encode("utf-8")
    return f"batch-replacement-{hashlib.sha256(raw).hexdigest()[:24]}"


def _load_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": "1.0",
            "source": "current_batch_official_mapping_replacements",
            "replacements": [],
        }
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchMappingReplacementError(
            f"无法读取当前批次映射替换记录：{exc}"
        ) from exc
    validate_batch_mapping_replacement_document(document)
    return document


def save_batch_mapping_replacement(
    output_path: str | Path,
    catalog: "ProductCatalog",
    *,
    company_id: str,
    source_profile_id: str,
    identifier_type: str,
    source_value: str,
    source_spec: str,
    expected_product_code: str,
    expected_spec_code: str,
    replacement_product_code: str,
    replacement_spec_code: str,
    source_fingerprint: str,
    reason: str,
    confirmed_by: str,
    confirmation_token: str,
) -> Path:
    if confirmation_token != BATCH_REPLACEMENT_CONFIRMATION:
        raise BatchMappingReplacementError("缺少当前批次替换正式映射的精确确认口令")

    company_id = _clean(company_id)
    source_profile_id = _clean(source_profile_id)
    identifier_type = _clean(identifier_type)
    source_value = _clean(source_value)
    source_spec = _clean(source_spec)
    source_fingerprint = _clean(source_fingerprint)
    confirmed_by = _clean(confirmed_by)
    reason = _clean(reason)
    if not company_id or not source_profile_id:
        raise BatchMappingReplacementError("公司和来源配置不能为空")
    if identifier_type not in ALLOWED_IDENTIFIER_TYPES:
        raise BatchMappingReplacementError("来源商品标识类型不受支持")
    if not source_value and not source_spec:
        raise BatchMappingReplacementError("来源商品标识和来源规格不能同时为空")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_fingerprint):
        raise BatchMappingReplacementError("临时替换必须绑定sha256来源文件指纹")
    if not confirmed_by or not reason:
        raise BatchMappingReplacementError("确认人和替换原因不能为空")

    official = catalog.resolve_official_mapping_target(
        source_profile_id=source_profile_id,
        identifier_type=identifier_type,
        source_value=source_value,
        source_spec=source_spec,
    )
    if official is None:
        raise BatchMappingReplacementError("没有找到可被当前批次替换的唯一正式映射")
    expected = catalog.resolve_unique_target(expected_product_code, expected_spec_code)
    if expected is None or (
        official.product_code,
        official.spec_code,
    ) != (expected.product_code, expected.spec_code):
        raise BatchMappingReplacementError("记录的旧目标与当前正式映射不一致，请重新核对")
    replacement = catalog.resolve_unique_target(
        replacement_product_code,
        replacement_spec_code,
    )
    if replacement is None:
        raise BatchMappingReplacementError("替换目标在有效商品目录中不存在或不唯一")
    if (
        replacement.product_code,
        replacement.spec_code,
    ) == (official.product_code, official.spec_code):
        raise BatchMappingReplacementError("替换目标与当前正式映射相同，无需创建临时替换")

    now = datetime.now(timezone.utc).isoformat()
    replacement_id = _replacement_id(
        source_profile_id,
        identifier_type,
        source_value,
        source_spec,
        source_fingerprint,
    )
    record = {
        "replacement_id": replacement_id,
        "company_id": company_id,
        "source_profile_id": source_profile_id,
        "identifier_type": identifier_type,
        "source_value": source_value,
        "source_spec": source_spec,
        "expected_product_code": official.product_code,
        "expected_spec_code": official.spec_code,
        "replacement_product_code": replacement.product_code,
        "replacement_spec_code": replacement.spec_code,
        "source_fingerprint": source_fingerprint,
        "scope": "current_batch",
        "confirmed_by": confirmed_by,
        "confirmed_at": now,
        "reason": reason,
    }
    path = Path(output_path).expanduser().resolve()
    document = _load_document(path)
    records = [
        item
        for item in document["replacements"]
        if _clean(item.get("replacement_id")) != replacement_id
    ]
    records.append(record)
    document["replacements"] = records
    document["updated_at"] = now
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path
