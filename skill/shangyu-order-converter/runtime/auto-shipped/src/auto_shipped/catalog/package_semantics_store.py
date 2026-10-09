from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .product_catalog import clean_identifier


PACKAGE_SEMANTICS_REUSE_CONFIRMATION = "CONFIRM_PACKAGE_SEMANTICS_REUSE"
ALLOWED_SEMANTIC_TYPES = {
    "single_stick_packaged",
    "eight_stick_family_pack",
    "pack_content_count",
    "bundle",
}
ALLOWED_QUANTITY_STRATEGIES = {
    "same_as_source",
    "divide_by_sticks_per_target_unit",
    "requires_bundle_expansion",
}


class PackageSemanticsStoreError(ValueError):
    pass


def _normalized(value: Any) -> str:
    return clean_identifier(value).casefold()


def _rule_key(
    source_profile_id: str,
    product_family: str,
    source_expression: str,
) -> str:
    raw = "\x1f".join(
        [
            _normalized(source_profile_id),
            _normalized(product_family),
            _normalized(source_expression),
        ]
    ).encode("utf-8")
    return f"pkg-rule-{hashlib.sha256(raw).hexdigest()[:20]}"


def _proposal_id(
    rule_key: str,
    semantic_type: str,
    quantity_strategy: str,
    sticks_per_target_unit: int | None,
) -> str:
    raw = "\x1f".join(
        [
            rule_key,
            semantic_type,
            quantity_strategy,
            str(sticks_per_target_unit or ""),
        ]
    ).encode("utf-8")
    return f"pkg-proposal-{hashlib.sha256(raw).hexdigest()[:20]}"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def save_package_semantics_proposal(
    state_dir: str | Path,
    *,
    company_id: str,
    source_profile_id: str,
    product_family: str,
    source_expression: str,
    semantic_type: str,
    quantity_strategy: str,
    sticks_per_target_unit: int | None = None,
    confirmation_token: str,
) -> dict[str, Any]:
    if confirmation_token != PACKAGE_SEMANTICS_REUSE_CONFIRMATION:
        raise PackageSemanticsStoreError("缺少‘以后沿用’包装语义的精确确认口令")

    fields = {
        "company_id": clean_identifier(company_id),
        "source_profile_id": clean_identifier(source_profile_id),
        "product_family": clean_identifier(product_family),
        "source_expression": clean_identifier(source_expression),
        "semantic_type": clean_identifier(semantic_type),
        "quantity_strategy": clean_identifier(quantity_strategy),
    }
    if any(not value for value in fields.values()):
        raise PackageSemanticsStoreError("包装语义提案的公司、来源、品类、表达和换算均不能为空")
    if fields["semantic_type"] not in ALLOWED_SEMANTIC_TYPES:
        raise PackageSemanticsStoreError("不支持的包装语义类型")
    if fields["quantity_strategy"] not in ALLOWED_QUANTITY_STRATEGIES:
        raise PackageSemanticsStoreError("不支持的数量换算策略")
    if fields["quantity_strategy"] == "divide_by_sticks_per_target_unit":
        if not sticks_per_target_unit or sticks_per_target_unit <= 0:
            raise PackageSemanticsStoreError("按根数换算包装时必须提供正整数每件根数")
    elif sticks_per_target_unit is not None and sticks_per_target_unit <= 0:
        raise PackageSemanticsStoreError("每件根数必须为正整数")

    rule_key = _rule_key(
        fields["source_profile_id"],
        fields["product_family"],
        fields["source_expression"],
    )
    proposal_id = _proposal_id(
        rule_key,
        fields["semantic_type"],
        fields["quantity_strategy"],
        sticks_per_target_unit,
    )
    payload: dict[str, Any] = {
        "schema_version": "package-semantics-proposal/1.0",
        "proposal_id": proposal_id,
        "rule_key": rule_key,
        "status": "pending_owner_review",
        **fields,
        "sticks_per_target_unit": sticks_per_target_unit,
        "confirmed_scope": "future_reuse",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "privacy": "contains_no_order_recipient_or_address_data",
    }
    path = Path(state_dir).expanduser().resolve() / f"{proposal_id}.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        comparable_keys = {
            "proposal_id",
            "rule_key",
            "company_id",
            "source_profile_id",
            "product_family",
            "source_expression",
            "semantic_type",
            "quantity_strategy",
            "sticks_per_target_unit",
        }
        if any(existing.get(key) != payload.get(key) for key in comparable_keys):
            raise PackageSemanticsStoreError("同一提案ID已有不同内容，拒绝覆盖")
        return {**existing, "storage_path": str(path), "reused": True}
    _write_json_atomic(path, payload)
    return {**payload, "storage_path": str(path), "reused": False}


def list_package_semantics_proposals(state_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(state_dir).expanduser().resolve()
    if not root.is_dir():
        return []
    proposals: list[dict[str, Any]] = []
    for path in sorted(root.glob("pkg-proposal-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackageSemanticsStoreError(f"无法读取包装语义提案 {path.name}: {exc}") from exc
        if payload.get("schema_version") != "package-semantics-proposal/1.0":
            raise PackageSemanticsStoreError(f"包装语义提案版本不受支持: {path.name}")
        proposals.append(payload)
    return proposals


def export_package_semantics_proposals(
    state_dir: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    proposals = list_package_semantics_proposals(state_dir)
    by_rule: dict[str, set[tuple[Any, ...]]] = {}
    for item in proposals:
        signature = (
            item.get("semantic_type"),
            item.get("quantity_strategy"),
            item.get("sticks_per_target_unit"),
        )
        by_rule.setdefault(str(item.get("rule_key") or ""), set()).add(signature)
    conflicts = sorted(key for key, values in by_rule.items() if len(values) > 1)
    bundle = {
        "schema_version": "package-semantics-proposal-bundle/1.0",
        "status": "needs_owner_review" if proposals else "empty",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "proposal_count": len(proposals),
        "conflicting_rule_keys": conflicts,
        "proposals": proposals,
        "privacy": "contains_no_order_recipient_or_address_data",
    }
    output = Path(output_path).expanduser().resolve()
    _write_json_atomic(output, bundle)
    return {**bundle, "output": str(output)}
