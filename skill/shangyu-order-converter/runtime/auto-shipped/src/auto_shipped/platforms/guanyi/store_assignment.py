from __future__ import annotations

from typing import Any, Iterable

from auto_shipped.platforms.guanyi.order_policies import (
    ResolvedOrderItem,
    matches_resolved_item,
)


class GuanyiStoreAssignmentError(ValueError):
    """Raised when a store cannot be assigned from confirmed product rules."""


def select_store(
    resolved_items: Iterable[ResolvedOrderItem],
    rules: dict[str, Any],
) -> str:
    """Resolve one store for every item; fail closed on gaps or conflicts."""

    policy = rules.get("store_assignment_policy") or {}
    strategy = str(policy.get("strategy") or "")
    if strategy != "all_items_same_store_by_product":
        raise GuanyiStoreAssignmentError(f"未知店铺分配策略: {strategy or '未配置'}")

    assignment_rules = policy.get("rules") or []
    if not assignment_rules:
        raise GuanyiStoreAssignmentError("店铺分配策略没有已登记的商品规则")

    items = tuple(resolved_items)
    if not items:
        raise GuanyiStoreAssignmentError("订单没有可用于店铺分配的商品")

    stores: set[str] = set()
    for item in items:
        matched_stores = {
            str(rule.get("store") or "").strip()
            for rule in assignment_rules
            if matches_resolved_item(item, rule.get("match") or {})
            and str(rule.get("store") or "").strip()
        }
        if not matched_stores:
            raise GuanyiStoreAssignmentError(
                f"商品代码{item.product_code}、规格代码{item.spec_code}没有已确认店铺规则"
            )
        if len(matched_stores) > 1:
            raise GuanyiStoreAssignmentError(
                f"商品代码{item.product_code}、规格代码{item.spec_code}命中多个店铺"
            )
        stores.update(matched_stores)

    if len(stores) != 1:
        raise GuanyiStoreAssignmentError("同一订单的商品被分配到不同店铺")
    return next(iter(stores))
