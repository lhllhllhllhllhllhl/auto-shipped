from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from auto_shipped.catalog import ProductCatalog, resolve_target_hint


class ProductDecisionError(ValueError):
    """Raised when a target correction cannot be bound to a prior decision."""


@dataclass(frozen=True, slots=True)
class ProductDecisionResolution:
    payload: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.payload.get("status") or "")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _decision_bundle(document: Mapping[str, Any]) -> Mapping[str, Any]:
    bundle = document.get("decision_bundle")
    if isinstance(bundle, Mapping):
        return bundle
    if "items" in document:
        return document
    raise ProductDecisionError("输入文件不包含decision_bundle。")


def _source_identity(proposed: Mapping[str, Any]) -> dict[str, str]:
    source_type = str(proposed.get("source_identifier_type") or "").strip()
    source_code = str(proposed.get("source_product_code") or "").strip()
    source_name = str(proposed.get("source_product_name") or "").strip()
    source_spec = str(proposed.get("source_spec") or "").strip()
    source_value = source_name if source_type == "product_name" else source_code
    if not source_type or (not source_value and not source_spec):
        raise ProductDecisionError("待确认项缺少稳定的来源商品身份。")
    return {
        "company_id": str(proposed.get("company_id") or "").strip(),
        "source_profile_id": str(proposed.get("source_profile_id") or "").strip(),
        "identifier_type": source_type,
        "source_value": source_value,
        "source_spec": source_spec,
        "source_fingerprint": str(proposed.get("source_fingerprint") or "").strip(),
    }


def _source_hash(source_identity: Mapping[str, str]) -> str:
    canonical = json.dumps(
        dict(source_identity),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def resolve_product_mapping_decision(
    document: Mapping[str, Any],
    catalog: ProductCatalog,
    *,
    decision_id: str,
    target_hint: str,
    hint_type: str = "auto",
    reuse_scope: str = "current_batch",
    mapping_scope: str = "source",
) -> ProductDecisionResolution:
    """Resolve a user reply against an immutable prior product decision.

    The source side is copied exclusively from the existing decision bundle.
    The user's reply is always treated as a target-side hint here; callers must
    use the separate text-intake correction path when the user explicitly says
    that the original order itself was extracted incorrectly.
    """

    if reuse_scope not in {"current_batch", "future"}:
        raise ProductDecisionError(f"未知reuse_scope：{reuse_scope}")
    if mapping_scope not in {"source", "global"}:
        raise ProductDecisionError(f"未知mapping_scope：{mapping_scope}")
    if reuse_scope == "current_batch" and mapping_scope == "global":
        raise ProductDecisionError("仅本次使用不能写成全局映射。")

    bundle = _decision_bundle(document)
    matching = [
        item
        for item in bundle.get("items") or []
        if isinstance(item, Mapping)
        and str(item.get("decision_id") or "").strip() == decision_id
    ]
    if len(matching) != 1:
        raise ProductDecisionError(
            f"decision_id必须唯一命中一个待确认项，实际{len(matching)}项。"
        )
    item = matching[0]
    if str(item.get("code") or "") != "CONFIRM_GUANYI_PRODUCT_MAPPING":
        raise ProductDecisionError("该decision_id不是商品映射待确认项。")
    proposed = item.get("proposed_value")
    if not isinstance(proposed, Mapping):
        raise ProductDecisionError("商品映射待确认项缺少proposed_value。")
    source = _source_identity(proposed)
    if mapping_scope == "global" and source["identifier_type"] != "product_name":
        raise ProductDecisionError(
            "全局复用只允许明确商品名称别名；来源商品代码和条码只能按来源保存。"
        )
    if mapping_scope == "global" and not source["source_value"]:
        raise ProductDecisionError("全局名称别名必须包含明确的来源商品名称。")

    target = resolve_target_hint(
        catalog,
        target_hint,
        declared_type=hint_type,  # type: ignore[arg-type]
    )
    status = "resolved" if target.status == "resolved" else "needs_input"
    normalized_company = "*" if mapping_scope == "global" else source["company_id"]
    normalized_profile = "*" if mapping_scope == "global" else source["source_profile_id"]
    mapping_record = None
    if target.status == "resolved" and target.product is not None:
        mapping_record = {
            "company_id": normalized_company,
            "source_profile_id": normalized_profile,
            "identifier_type": source["identifier_type"],
            "source_value": source["source_value"],
            "source_spec": source["source_spec"],
            "target_platform": "guanyi",
            "product_code": target.product.product_code,
            "spec_code": target.product.spec_code,
        }
    payload = {
        "schema_version": "1.0",
        "status": status,
        "decision_id": decision_id,
        "source_identity": source,
        "source_identity_sha256": _source_hash(source),
        "target_hint": {
            "raw_value": str(target_hint or "").strip(),
            "declared_type": hint_type,
        },
        "target_resolution": target.to_dict(),
        "reuse_scope": reuse_scope,
        "mapping_scope": mapping_scope,
        "mapping_record": mapping_record,
        "safety": {
            "source_identity_copied_from_decision_bundle": True,
            "user_target_hint_never_rewrites_source": True,
            "fuzzy_candidate_requires_another_confirmation": target.status == "candidate",
            "may_persist": target.status == "resolved",
        },
    }
    return ProductDecisionResolution(payload)
