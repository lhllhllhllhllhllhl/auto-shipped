from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from auto_shipped.catalog import (
    ProductCatalog,
    ProductRecord,
    clean_identifier,
    normalize_product_text,
)
from auto_shipped.domain import ParsedItem


@dataclass(frozen=True, slots=True)
class ResolvedLineItem:
    """One target-platform line resolved from one source item."""

    source_item: ParsedItem
    product: ProductRecord
    quantity: float
    expansion_rule_id: str = ""
    output_product_name: str = ""


@dataclass(slots=True)
class ItemExpansionResult:
    applied: bool = False
    lines: list[ResolvedLineItem] = field(default_factory=list)
    error_code: str = ""
    reason: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error_code)


def _matches_source_item(item: ParsedItem, match: dict[str, Any]) -> bool:
    checks: list[bool] = []
    for key, actual in (
        ("source_product_code", item.source_product_code),
        ("source_product_name", item.source_product_name),
        ("source_spec", item.source_spec),
    ):
        expected = clean_identifier(match.get(key))
        if expected:
            checks.append(clean_identifier(actual) == expected)
    return bool(checks) and all(checks)


def _matches_registered_rule(
    item: ParsedItem,
    rule: dict[str, Any],
    *,
    company_id: str,
    source_profile_id: str,
    source_profile_aliases: tuple[str, ...] = (),
    source_fingerprint: str,
) -> bool:
    if clean_identifier(rule.get("company_id")) != clean_identifier(company_id):
        return False
    compatible_profiles = {
        clean_identifier(source_profile_id),
        *(clean_identifier(value) for value in source_profile_aliases),
    }
    if clean_identifier(rule.get("source_profile_id")) not in compatible_profiles:
        return False
    if clean_identifier(rule.get("target_platform")) != "guanyi":
        return False
    reuse_scope = clean_identifier(rule.get("reuse_scope")) or "future"
    if reuse_scope == "current_batch":
        if clean_identifier(rule.get("source_fingerprint")) != clean_identifier(
            source_fingerprint
        ):
            return False
    elif reuse_scope != "future":
        return False
    identifier_type = clean_identifier(rule.get("identifier_type"))
    expected_value = clean_identifier(rule.get("source_value"))
    if identifier_type == "product_name":
        value_matches = normalize_product_text(item.source_product_name) == (
            normalize_product_text(expected_value)
        )
    elif identifier_type in {"product_code", "barcode"}:
        value_matches = clean_identifier(item.source_product_code) == expected_value
    else:
        return False
    expected_spec = normalize_product_text(rule.get("source_spec"))
    actual_spec = normalize_product_text(item.source_spec)
    return value_matches and actual_spec == expected_spec


def _registered_rule_to_local(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": clean_identifier(rule.get("rule_id")),
        "components": list(rule.get("components") or []),
        "confirmed": rule.get("confirmed") is True,
    }


def _registered_component_signature(
    rule: dict[str, Any],
) -> tuple[tuple[str, str, float], ...]:
    values: list[tuple[str, str, float]] = []
    for component in rule.get("components") or []:
        try:
            multiplier = float(component.get("quantity_multiplier"))
        except (TypeError, ValueError):
            multiplier = 0
        values.append(
            (
                clean_identifier(component.get("product_code")),
                clean_identifier(component.get("spec_code")),
                multiplier,
            )
        )
    return tuple(values)


def _looks_like_unregistered_expansion(item: ParsedItem) -> bool:
    text = " ".join(
        filter(
            None,
            (
                clean_identifier(item.source_product_name),
                clean_identifier(item.source_spec),
                clean_identifier(item.source_display_spec),
            ),
        )
    )
    return bool(
        re.search(r"\d+\s*[根棒袋盒份套]?\s*\+\s*\d+", text)
        or any(marker in text for marker in ("组合商品", "配套商品", "加赠", "加送"))
    )


def expand_source_item(
    item: ParsedItem,
    catalog: ProductCatalog,
    rules: dict[str, Any],
    *,
    company_id: str = "",
    source_profile_id: str = "",
    source_profile_aliases: tuple[str, ...] = (),
    source_fingerprint: str = "",
    registered_rules: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
) -> ItemExpansionResult:
    """Apply a confirmed one-to-many source-item expansion before normal mapping."""

    policy = rules.get("item_expansion_policy") or {}
    strategy = str(policy.get("strategy") or "")
    if policy and strategy != "confirmed_source_item_expansions":
        return ItemExpansionResult(
            error_code="GUANYI_ITEM_EXPANSION_POLICY_INVALID",
            reason=f"未知组合商品展开策略: {strategy or '未配置'}",
        )

    registered_matched = [
        rule
        for rule in registered_rules
        if _matches_registered_rule(
            item,
            rule,
            company_id=company_id,
            source_profile_id=source_profile_id,
            source_profile_aliases=source_profile_aliases,
            source_fingerprint=source_fingerprint,
        )
    ]
    if len(registered_matched) > 1:
        signatures = {
            _registered_component_signature(rule) for rule in registered_matched
        }
        if len(signatures) > 1:
            return ItemExpansionResult(
                applied=True,
                error_code="GUANYI_ITEM_EXPANSION_AMBIGUOUS",
                reason="同一来源商品命中多个组成不同的正式组合商品规则",
            )
        registered_matched = [registered_matched[0]]
    matched = [
        rule
        for rule in policy.get("rules") or []
        if _matches_source_item(item, rule.get("match") or {})
    ]
    if registered_matched:
        matched = [_registered_rule_to_local(registered_matched[0])]
    if not matched:
        if str(policy.get("on_unmatched") or "standard_product_mapping") != (
            "standard_product_mapping"
        ):
            return ItemExpansionResult(
                error_code="GUANYI_ITEM_EXPANSION_POLICY_INVALID",
                reason="组合商品未命中时只允许回到标准商品映射",
            )
        if _looks_like_unregistered_expansion(item):
            return ItemExpansionResult(
                applied=True,
                error_code="GUANYI_ITEM_EXPANSION_UNCONFIRMED",
                reason=(
                    "来源商品包含组合/加赠迹象，但正式组合商品规则中没有精确记录；"
                    "需要确认目标商品、规格和数量倍数。"
                ),
            )
        return ItemExpansionResult()
    if len(matched) != 1:
        return ItemExpansionResult(
            applied=True,
            error_code="GUANYI_ITEM_EXPANSION_AMBIGUOUS",
            reason="同一来源商品命中多个组合商品展开规则",
        )

    rule = matched[0]
    rule_id = str(rule.get("rule_id") or "").strip()
    if not rule_id or not rule.get("confirmed", False):
        return ItemExpansionResult(
            applied=True,
            error_code="GUANYI_ITEM_EXPANSION_UNCONFIRMED",
            reason=f"组合商品展开规则未确认: {rule_id or '未命名规则'}",
        )

    components = rule.get("components") or []
    if not components:
        return ItemExpansionResult(
            applied=True,
            error_code="GUANYI_ITEM_EXPANSION_EMPTY",
            reason=f"组合商品展开规则没有目标商品: {rule_id}",
        )

    lines: list[ResolvedLineItem] = []
    for component in components:
        product_code = clean_identifier(component.get("product_code"))
        spec_code = clean_identifier(component.get("spec_code"))
        try:
            multiplier = float(component.get("quantity_multiplier"))
        except (TypeError, ValueError):
            multiplier = 0
        if not product_code or not spec_code or multiplier <= 0:
            return ItemExpansionResult(
                applied=True,
                error_code="GUANYI_ITEM_EXPANSION_COMPONENT_INVALID",
                reason=f"组合商品展开规则包含无效目标或数量倍数: {rule_id}",
            )
        product = catalog.get_unique_pair(product_code, spec_code)
        if product is None:
            return ItemExpansionResult(
                applied=True,
                error_code="GUANYI_ITEM_EXPANSION_TARGET_NOT_FOUND",
                reason=(
                    f"组合商品展开目标在管易商品主档中不是唯一组合: "
                    f"{product_code} / {spec_code}"
                ),
            )
        lines.append(
            ResolvedLineItem(
                source_item=item,
                product=product,
                quantity=item.quantity * multiplier,
                expansion_rule_id=rule_id,
                output_product_name=str(
                    component.get("output_product_name") or ""
                ).strip(),
            )
        )
    return ItemExpansionResult(applied=True, lines=lines)
