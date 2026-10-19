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
    *,
    source_extensions: dict[str, Any] | None = None,
    source_company_id: str = "",
    source_profile_id: str = "",
    manual_store_routes: Iterable[dict[str, Any]] = (),
) -> str:
    """Resolve one store for every item; fail closed on gaps or conflicts."""

    policy = rules.get("store_assignment_policy") or {}
    strategy = str(policy.get("strategy") or "")
    if strategy == "fixed":
        if policy.get("confirmed") is not True:
            raise GuanyiStoreAssignmentError("固定店铺策略尚未确认")
        store = str(policy.get("store") or "").strip()
        if not store:
            raise GuanyiStoreAssignmentError("固定店铺策略缺少店铺名称")
        return store
    if strategy == "source_extension_exact_map":
        field = str(policy.get("source_extension_field") or "").strip()
        if not field:
            raise GuanyiStoreAssignmentError("来源字段店铺策略缺少字段名")
        source_value = str((source_extensions or {}).get(field) or "").strip()
        if not source_value:
            raise GuanyiStoreAssignmentError(f"订单缺少店铺来源字段{field}")
        matched_stores = {
            str(rule.get("store") or "").strip()
            for rule in policy.get("rules") or []
            if source_value in {
                str(value).strip() for value in rule.get("source_values") or []
            }
            and str(rule.get("store") or "").strip()
        }
        if not matched_stores:
            raise GuanyiStoreAssignmentError(
                f"来源字段{field}的值“{source_value}”没有已确认店铺规则"
            )
        if len(matched_stores) > 1:
            raise GuanyiStoreAssignmentError(
                f"来源字段{field}的值“{source_value}”命中多个店铺"
            )
        return next(iter(matched_stores))
    if strategy in {
        "manual_store_route_table",
        "manual_store_route_then_product",
    }:
        if policy.get("confirmed") is not True:
            raise GuanyiStoreAssignmentError("手工单商品店铺关联策略尚未确认")
        items = tuple(resolved_items)
        if not items:
            raise GuanyiStoreAssignmentError("订单没有可用于店铺分配的商品")
        company = str(source_company_id or "").strip()
        profile = str(source_profile_id or "").strip()
        include_company_scope = policy.get("include_company_scope") is True
        include_source_profile_scope = policy.get(
            "include_source_profile_scope", True
        ) is not False
        fallback_scopes = tuple(
            str(value).strip()
            for value in policy.get("fallback_scope_ids") or ["manual_all"]
            if str(value).strip()
        )
        allowed_stores = {
            str(value).strip()
            for value in policy.get("allowed_stores") or []
            if str(value).strip()
        }
        active_routes = [
            route
            for route in manual_store_routes
            if str(route.get("status") or "active").strip() == "active"
        ]
        stores: set[str] = set()
        for item in items:
            matched_store = ""
            lookup_scopes = tuple(
                dict.fromkeys(
                    (
                        *((company,) if include_company_scope and company else ()),
                        *((profile,) if include_source_profile_scope and profile else ()),
                        *fallback_scopes,
                    )
                )
            )
            for scope in lookup_scopes:
                if not scope:
                    continue
                matches = {
                    str(route.get("store") or "").strip()
                    for route in active_routes
                    if str(route.get("scope_id") or "").strip() == scope
                    and str(route.get("product_code") or "").strip()
                    == item.product_code
                    and str(route.get("spec_code") or "").strip() == item.spec_code
                    and str(route.get("store") or "").strip()
                }
                if len(matches) > 1:
                    raise GuanyiStoreAssignmentError(
                        f"商品代码{item.product_code}、规格代码{item.spec_code}在作用域{scope}命中多个店铺"
                    )
                if matches:
                    matched_store = next(iter(matches))
                    break
            if not matched_store:
                product_fallback_stores = {
                    str(rule.get("store") or "").strip()
                    for rule in policy.get("product_fallback_rules") or []
                    if matches_resolved_item(item, rule.get("match") or {})
                    and str(rule.get("store") or "").strip()
                }
                if len(product_fallback_stores) > 1:
                    raise GuanyiStoreAssignmentError(
                        f"商品代码{item.product_code}、规格代码{item.spec_code}命中多个兜底店铺"
                    )
                if product_fallback_stores:
                    matched_store = next(iter(product_fallback_stores))
            if not matched_store:
                raise GuanyiStoreAssignmentError(
                    f"商品代码{item.product_code}、规格代码{item.spec_code}没有已确认的手工单店铺关联或商品兜底规则"
                )
            if allowed_stores and matched_store not in allowed_stores:
                raise GuanyiStoreAssignmentError(
                    f"店铺“{matched_store}”不在手工单允许店铺清单中"
                )
            stores.add(matched_store)
        if len(stores) != 1:
            raise GuanyiStoreAssignmentError("同一手工订单的商品被分配到不同店铺")
        return next(iter(stores))
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
