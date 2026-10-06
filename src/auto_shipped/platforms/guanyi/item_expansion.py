from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from auto_shipped.catalog import ProductCatalog, ProductRecord, clean_identifier
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


def expand_source_item(
    item: ParsedItem,
    catalog: ProductCatalog,
    rules: dict[str, Any],
) -> ItemExpansionResult:
    """Apply a confirmed one-to-many source-item expansion before normal mapping."""

    policy = rules.get("item_expansion_policy") or {}
    if not policy:
        return ItemExpansionResult()
    strategy = str(policy.get("strategy") or "")
    if strategy != "confirmed_source_item_expansions":
        return ItemExpansionResult(
            error_code="GUANYI_ITEM_EXPANSION_POLICY_INVALID",
            reason=f"未知组合商品展开策略: {strategy or '未配置'}",
        )

    matched = [
        rule
        for rule in policy.get("rules") or []
        if _matches_source_item(item, rule.get("match") or {})
    ]
    if not matched:
        if str(policy.get("on_unmatched") or "standard_product_mapping") != (
            "standard_product_mapping"
        ):
            return ItemExpansionResult(
                error_code="GUANYI_ITEM_EXPANSION_POLICY_INVALID",
                reason="组合商品未命中时只允许回到标准商品映射",
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
