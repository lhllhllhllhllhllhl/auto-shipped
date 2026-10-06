from __future__ import annotations

import re
from collections import Counter

from auto_shipped.domain import CanonicalOrder, Issue


PHONE_RE = re.compile(r"^1\d{10}$")


def validate_order(order: CanonicalOrder) -> list[Issue]:
    issues: list[Issue] = []
    required = {
        "platform_order_no": order.platform_order_no,
        "buyer_member": order.buyer_member,
        "store_name": order.store_name,
        "warehouse_name": order.warehouse_name,
        "recipient.name": order.recipient.name,
        "recipient.province": order.recipient.province,
        "recipient.city": order.recipient.city,
        "recipient.district": order.recipient.district,
        "recipient.detail": order.recipient.detail,
    }
    for field_name, value in required.items():
        if not str(value or "").strip():
            issues.append(
                Issue(
                    code="MISSING_REQUIRED_FIELD",
                    severity="error",
                    message=f"缺少必填字段: {field_name}",
                    field=field_name,
                    source_ref=order.source.row_ref,
                )
            )
    if not PHONE_RE.fullmatch(order.recipient.mobile):
        issues.append(
            Issue(
                code="INVALID_MOBILE",
                severity="error",
                message="收件手机号不是11位中国大陆手机号",
                field="recipient.mobile",
                source_ref=order.source.row_ref,
            )
        )
    if not order.items:
        issues.append(
            Issue(
                code="MISSING_ITEMS",
                severity="error",
                message="订单没有商品明细",
                field="items",
                source_ref=order.source.row_ref,
            )
        )
    for index, item in enumerate(order.items):
        if item.quantity <= 0:
            issues.append(
                Issue(
                    code="INVALID_QUANTITY",
                    severity="error",
                    message="商品数量必须大于0",
                    field=f"items[{index}].quantity",
                    source_ref=order.source.row_ref,
                )
            )
        if item.mapping_status != "confirmed" and not any(
            existing.field == f"items[{index}]" for existing in order.issues
        ):
            issues.append(
                Issue(
                    code="UNRESOLVED_ITEM",
                    severity="error",
                    message="商品映射尚未确认",
                    field=f"items[{index}]",
                    source_ref=order.source.row_ref,
                )
            )
    return issues


def validate_order_batch(orders: list[CanonicalOrder]) -> list[Issue]:
    issues: list[Issue] = []
    platform_counts = Counter(order.platform_order_no for order in orders if order.platform_order_no)
    source_counts = Counter(order.source.source_order_id for order in orders if order.source.source_order_id)
    for value, count in platform_counts.items():
        if count > 1:
            issues.append(
                Issue(
                    code="DUPLICATE_PLATFORM_ORDER_NO",
                    severity="error",
                    message=f"平台单号重复 {count} 次",
                    field="platform_order_no",
                    details={"value": value, "count": count},
                )
            )
    for value, count in source_counts.items():
        if count > 1:
            issues.append(
                Issue(
                    code="DUPLICATE_SOURCE_ORDER_ID",
                    severity="error",
                    message=f"来源单号重复 {count} 次",
                    field="source.source_order_id",
                    details={"value": value, "count": count},
                )
            )
    return issues

