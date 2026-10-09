from __future__ import annotations

import re
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


@dataclass(frozen=True, slots=True)
class RoutedOrderNotes:
    recipient_address: str
    seller_source_note: str
    courier_instructions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedContactColumns:
    phone: str
    mobile: str
    appended_suffix: str = ""


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
        "source_override_then_default",
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
    if strategy == "source_override_then_default":
        if not default_carrier:
            raise GuanyiOrderPolicyError("管易物流规则缺少默认物流")
        return normalized_source_carrier or default_carrier
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


def apply_contact_suffix_policy(
    order: ParsedOrder,
    contact_phone: str,
    contact_mobile: str,
    policy: dict[str, Any] | None,
) -> ResolvedContactColumns:
    """Append a verified recipient marker to contact fields for scoped channels."""

    if not policy:
        return ResolvedContactColumns(contact_phone, contact_mobile)
    if not policy.get("confirmed", False):
        raise GuanyiOrderPolicyError("联系电话尾码规则尚未确认")
    strategy = str(policy.get("strategy") or "").strip()
    if strategy != "append_verified_recipient_marker":
        raise GuanyiOrderPolicyError(
            f"未知联系电话尾码策略: {strategy or '未配置'}"
        )

    channel_field = str(policy.get("source_channel_field") or "source_channel")
    source_channel = str(order.source_extensions.get(channel_field) or "").strip()
    allowed_channels = {str(value).strip() for value in policy.get("source_channels") or []}
    source_carrier = (
        order.shipment_facts.carrier_name.strip()
        if order.shipment_facts and order.shipment_facts.carrier_name
        else ""
    )
    allowed_carriers = {str(value).strip() for value in policy.get("source_carriers") or []}
    if allowed_channels and source_channel not in allowed_channels:
        return ResolvedContactColumns(contact_phone, contact_mobile)
    if allowed_carriers and source_carrier not in allowed_carriers:
        return ResolvedContactColumns(contact_phone, contact_mobile)

    pattern_text = str(policy.get("marker_pattern") or "").strip()
    if not pattern_text:
        raise GuanyiOrderPolicyError("联系电话尾码规则缺少四位码提取表达式")
    try:
        marker_pattern = re.compile(pattern_text)
    except re.error as exc:
        raise GuanyiOrderPolicyError("联系电话尾码规则的提取表达式无效") from exc

    def extract_marker(value: str) -> str:
        match = marker_pattern.search(str(value or "").strip())
        if not match:
            return ""
        if "code" in match.groupdict():
            return str(match.group("code") or "")
        return str(match.group(1) or "") if match.groups() else ""

    explicit_field = str(policy.get("explicit_extension_field") or "").strip()
    explicit_marker = (
        str(order.source_extensions.get(explicit_field) or "").strip()
        if explicit_field
        else ""
    )
    name_marker = extract_marker(order.recipient.name)
    address_marker = extract_marker(order.recipient.full_address)
    if policy.get("require_recipient_name_marker", False) and not name_marker:
        raise GuanyiOrderPolicyError("京东中通订单的收货人姓名后缺少四位码")

    markers = {value for value in (explicit_marker, name_marker) if value}
    if policy.get("cross_check_address_marker_if_present", False) and address_marker:
        markers.add(address_marker)
    if not markers:
        raise GuanyiOrderPolicyError("京东中通订单缺少可核验的四位码")
    if len(markers) != 1:
        raise GuanyiOrderPolicyError("京东中通订单的姓名、地址或结构化四位码不一致")
    marker = next(iter(markers))
    if not re.fullmatch(r"\d{4}", marker):
        raise GuanyiOrderPolicyError("京东中通订单的联系电话尾码必须是四位数字")

    separator = str(policy.get("separator") or "-")

    def append_marker(contact: str) -> str:
        value = str(contact or "").strip()
        if not value:
            return ""
        existing = re.search(r"-(\d{1,6})$", value)
        if existing:
            if existing.group(1) != marker:
                raise GuanyiOrderPolicyError("联系电话已有尾码与姓名后的四位码不一致")
            return value
        return f"{value}{separator}{marker}"

    return ResolvedContactColumns(
        phone=append_marker(contact_phone),
        mobile=append_marker(contact_mobile),
        appended_suffix=marker,
    )


def _note_segments(value: str | None, split_pattern: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in re.split(split_pattern, text) if part.strip()]


def _trim_note_wrapper(value: str, open_wrapper: str, close_wrapper: str) -> str:
    text = value.strip()
    wrapper_pairs = ((open_wrapper, close_wrapper), ("（", "）"), ("(", ")"))
    for left, right in wrapper_pairs:
        if left and right and text.startswith(left) and text.endswith(right):
            return text[len(left) : -len(right)].strip()
    return text


def route_order_notes(
    recipient_address: str,
    source_note: str | None,
    policy: dict[str, Any],
    *,
    explicit_delivery_instruction: str | Iterable[str] | None = None,
) -> RoutedOrderNotes:
    """Route courier-facing instructions to the address and keep other notes for sellers."""

    strategy = str(policy.get("strategy") or "").strip()
    if strategy != "courier_instructions_to_address_other_notes_to_seller":
        raise GuanyiOrderPolicyError(f"未知备注分流策略: {strategy or '未配置'}")
    if not policy.get("confirmed", False):
        raise GuanyiOrderPolicyError("备注分流策略尚未确认")

    split_pattern = str(policy.get("split_pattern") or r"[；;，,\n\r]+")
    keywords = [
        "".join(unicodedata.normalize("NFKC", str(value or "")).split())
        for value in policy.get("courier_keywords") or []
        if str(value or "").strip()
    ]
    if not keywords:
        raise GuanyiOrderPolicyError("备注分流策略缺少快递员指令关键词")

    open_wrapper = str(policy.get("address_note_open") or "（")
    close_wrapper = str(policy.get("address_note_close") or "）")
    separator = str(policy.get("separator") or "；")
    courier_parts: list[str] = []
    seller_parts: list[str] = []

    explicit_parts: list[str] = []
    if isinstance(explicit_delivery_instruction, str):
        explicit_parts.extend(_note_segments(explicit_delivery_instruction, split_pattern))
    elif isinstance(explicit_delivery_instruction, (list, tuple, set)):
        for item in explicit_delivery_instruction:
            explicit_parts.extend(_note_segments(str(item or ""), split_pattern))
    elif explicit_delivery_instruction is not None:
        raise GuanyiOrderPolicyError("显式快递员指令必须是文字或文字列表")

    def append_unique(target: list[str], raw_value: str) -> None:
        cleaned = _trim_note_wrapper(raw_value, open_wrapper, close_wrapper)
        if cleaned and cleaned not in target:
            target.append(cleaned)

    for part in explicit_parts:
        append_unique(courier_parts, part)

    for part in _note_segments(source_note, split_pattern):
        normalized = "".join(unicodedata.normalize("NFKC", part).split())
        if any(keyword in normalized for keyword in keywords):
            append_unique(courier_parts, part)
        else:
            append_unique(seller_parts, part)

    address = str(recipient_address or "").strip()
    pending_for_address = [part for part in courier_parts if part not in address]
    if pending_for_address:
        address = f"{address}{open_wrapper}{separator.join(pending_for_address)}{close_wrapper}"

    return RoutedOrderNotes(
        recipient_address=address,
        seller_source_note=separator.join(seller_parts),
        courier_instructions=tuple(courier_parts),
    )
