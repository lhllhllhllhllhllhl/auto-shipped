from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from auto_shipped.domain import ParsedOrder


class GuanyiOrderPolicyError(ValueError):
    """Raised when an order policy is missing or cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class ResolvedOrderItem:
    quantity: float
    product_code: str
    spec_code: str
    product_name: str
    spec_name: str

    @property
    def searchable_text(self) -> str:
        return f"{self.product_name} {self.spec_name}".strip()


def select_buyer_member(
    order: ParsedOrder,
    rules: dict[str, Any],
    resolved_items: Iterable[ResolvedOrderItem] = (),
    *,
    business_date: date | None = None,
) -> str:
    """Resolve buyer member using a source-level deterministic policy."""

    policy = rules.get("buyer_member_policy") or {}
    if not policy:
        legacy = ((rules.get("defaults") or {}).get("buyer_member") or {}).get("value")
        value = str(legacy or "").strip()
        if value:
            return value
        raise GuanyiOrderPolicyError("管易买家会员规则缺失")

    strategy = str(policy.get("strategy") or "")
    if strategy == "fixed":
        value = str(policy.get("value") or "").strip()
        if not value:
            raise GuanyiOrderPolicyError("固定买家会员不能为空")
        return value
    if strategy == "source_extension_or_default":
        field = str(policy.get("source_extension_field") or "").strip()
        default_value = str(policy.get("default_value") or "").strip()
        if not field or not default_value:
            raise GuanyiOrderPolicyError("来源覆盖买家会员规则缺少字段名或默认值")
        source_value = str(order.source_extensions.get(field) or "").strip()
        return source_value or default_value
    if strategy == "sam_product_type_date_group":
        prefix = str(policy.get("platform_prefix") or "").strip()
        group_field = str(
            policy.get("group_number_field")
            or policy.get("source_extension_field")
            or ""
        ).strip()
        group_number = str(order.source_extensions.get(group_field) or "").strip()
        if not prefix or not group_field:
            raise GuanyiOrderPolicyError("SAM买家会员规则缺少平台前缀或跟团号字段")
        if not group_number:
            raise GuanyiOrderPolicyError("SAM订单缺少跟团号，无法生成买家会员")

        items = tuple(resolved_items)
        if not items:
            raise GuanyiOrderPolicyError("SAM买家会员规则缺少已解析的商品")
        type_rules = policy.get("product_type_rules") or []
        item_type_codes: list[str] = []
        for item in items:
            matched_codes = {
                str(rule.get("type_code") or "").strip()
                for rule in type_rules
                if rule.get("confirmed", False)
                and matches_resolved_item(item, rule.get("match") or {})
                and str(rule.get("type_code") or "").strip()
            }
            if not matched_codes:
                raise GuanyiOrderPolicyError(
                    f"商品{item.product_code}/{item.spec_code}没有已确认的SAM商品类型缩写"
                )
            if len(matched_codes) != 1:
                raise GuanyiOrderPolicyError(
                    f"商品{item.product_code}/{item.spec_code}命中多个SAM商品类型缩写"
                )
            item_type_codes.append(next(iter(matched_codes)))
        distinct_codes = set(item_type_codes)
        if len(distinct_codes) != 1:
            raise GuanyiOrderPolicyError(
                "同一SAM订单包含多个商品类型，无法生成唯一买家会员"
            )
        type_code = next(iter(distinct_codes))

        current_date = business_date
        if current_date is None:
            timezone_name = str(policy.get("timezone") or "Asia/Shanghai").strip()
            try:
                current_date = datetime.now(ZoneInfo(timezone_name)).date()
            except ZoneInfoNotFoundError as exc:
                raise GuanyiOrderPolicyError(
                    f"SAM买家会员规则的时区无效: {timezone_name}"
                ) from exc
        date_format = str(policy.get("date_format") or "%y%m%d")
        date_value = current_date.strftime(date_format)
        separator = str(policy.get("group_separator") or "_")
        return f"{prefix}{type_code}{date_value}{separator}{group_number}"
    raise GuanyiOrderPolicyError(f"未知管易买家会员策略: {strategy or '未配置'}")


def matches_resolved_item(item: ResolvedOrderItem, match: dict[str, Any]) -> bool:
    product_codes = set(match.get("product_codes") or [])
    if product_codes and item.product_code not in product_codes:
        return False

    spec_codes = set(match.get("spec_codes") or [])
    if spec_codes and item.spec_code not in spec_codes:
        return False

    required_product_tokens = tuple(match.get("product_name_contains_all") or ())
    if required_product_tokens and not all(
        token in item.product_name for token in required_product_tokens
    ):
        return False

    any_text_tokens = tuple(match.get("any_text_contains") or ())
    if any_text_tokens and not any(
        token in item.searchable_text for token in any_text_tokens
    ):
        return False

    return bool(
        product_codes
        or spec_codes
        or required_product_tokens
        or any_text_tokens
    )


def select_logistics_carrier(
    order: ParsedOrder,
    resolved_items: Iterable[ResolvedOrderItem],
    rules: dict[str, Any],
) -> str:
    """Choose the carrier using source override first, then configured pack rules."""

    source_carrier = (
        order.shipment_facts.carrier_name.strip()
        if order.shipment_facts and order.shipment_facts.carrier_name
        else ""
    )
    policy = rules.get("logistics_policy") or {}
    strategy = str(policy.get("strategy") or "")
    if strategy not in {
        "source_override_then_confirmed_packaging_rules",
        "confirmed_packaging_rules_then_source_fallback",
    }:
        raise GuanyiOrderPolicyError(f"未知管易物流策略: {strategy or '未配置'}")
    source_carrier_priority = bool(policy.get("source_carrier_priority", False))
    normalized_source_carrier = ""
    if source_carrier:
        aliases = policy.get("source_carrier_aliases") or {}
        normalized_aliases = {
            unicodedata.normalize("NFKC", str(alias)).strip().casefold(): str(
                canonical
            ).strip()
            for alias, canonical in aliases.items()
            if str(alias).strip() and str(canonical).strip()
        }
        normalized_source_carrier = normalized_aliases.get(
            unicodedata.normalize("NFKC", source_carrier).strip().casefold(),
            "",
        )
        if not normalized_source_carrier:
            raise GuanyiOrderPolicyError(
                f"来源指定物流“{source_carrier}”没有已登记的管易标准名称"
            )
    if source_carrier and source_carrier_priority:
        return normalized_source_carrier
    default_carrier = str(policy.get("default_carrier") or "").strip()
    heavy_carrier = str(policy.get("heavy_carrier") or "").strip()
    conditions = policy.get("heavy_conditions") or []
    if not default_carrier or not heavy_carrier or not conditions:
        raise GuanyiOrderPolicyError("管易物流规则缺少默认物流、重货物流或重货条件")

    items = tuple(resolved_items)
    for condition in conditions:
        match = condition.get("match") or {}
        matched_quantity = sum(
            item.quantity for item in items if matches_resolved_item(item, match)
        )
        minimum_quantity = float(condition.get("minimum_quantity") or 0)
        if minimum_quantity <= 0:
            raise GuanyiOrderPolicyError("重货条件的最小数量必须大于0")
        if matched_quantity >= minimum_quantity:
            return heavy_carrier

    if source_carrier:
        return normalized_source_carrier
    return default_carrier


def compose_seller_remark(
    source_note: str | None,
    system_notes: Iterable[str] = (),
    *,
    separator: str = "；",
) -> str:
    """Compose seller remarks in stable order while removing exact duplicates."""

    parts: list[str] = []
    for value in (source_note, *system_notes):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return separator.join(parts)
