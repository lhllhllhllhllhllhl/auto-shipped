from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from auto_shipped.catalog import ProductCatalog, clean_identifier


class ManualStoreRouteError(ValueError):
    """Raised when a governed manual-order store route is invalid."""


def build_manual_store_route_key(
    *,
    scope_id: Any,
    product_code: Any,
    spec_code: Any = "",
) -> str:
    values = (
        clean_identifier(scope_id),
        clean_identifier(product_code),
        clean_identifier(spec_code),
    )
    if not values[0] or not values[1]:
        raise ManualStoreRouteError("店铺关联必须包含scope_id和product_code。")
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    return f"store-route-{digest[:24]}"


@dataclass(frozen=True, slots=True)
class ManualStoreRouteRecord:
    route_id: str
    route_key: str
    scope_id: str
    product_code: str
    spec_code: str
    store: str
    status: str
    route_version: str
    confirmed_by: str
    confirmed_at: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def normalize_manual_store_routes(
    records: Iterable[Mapping[str, Any]],
    *,
    catalog: ProductCatalog,
    repository_revision: str,
) -> list[dict[str, Any]]:
    revision = clean_identifier(repository_revision)
    if not revision:
        raise ManualStoreRouteError("正式库缺少mapping_revision。")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for source in records:
        status = clean_identifier(source.get("status"))
        if status == "inactive":
            continue
        if status != "active":
            raise ManualStoreRouteError(
                f"手工单店铺关联{source.get('route_id')!r}状态不是active/inactive。"
            )
        route_id = clean_identifier(source.get("route_id"))
        scope_id = clean_identifier(source.get("scope_id"))
        product_code = clean_identifier(source.get("product_code"))
        spec_code = clean_identifier(source.get("spec_code"))
        store = clean_identifier(source.get("store"))
        route_version = clean_identifier(source.get("route_version"))
        missing = [
            name
            for name, value in {
                "route_id": route_id,
                "scope_id": scope_id,
                "product_code": product_code,
                "store": store,
                "route_version": route_version,
            }.items()
            if not value
        ]
        if missing:
            raise ManualStoreRouteError(
                f"手工单店铺关联{route_id or '<unknown>'}缺少字段：{', '.join(missing)}"
            )
        target = catalog.resolve_unique_target(product_code, spec_code)
        if target is None:
            raise ManualStoreRouteError(
                f"手工单店铺关联{route_id}的商品/规格组合不存在或存在多个候选。"
            )
        expected_key = build_manual_store_route_key(
            scope_id=scope_id,
            product_code=target.product_code,
            spec_code=target.spec_code,
        )
        route_key = clean_identifier(source.get("route_key"))
        if route_key != expected_key:
            raise ManualStoreRouteError(
                f"手工单店铺关联{route_id}的route_key校验失败。"
            )
        if route_id in seen_ids:
            raise ManualStoreRouteError(f"手工单店铺关联route_id重复：{route_id}")
        if route_key in seen_keys:
            raise ManualStoreRouteError(f"手工单店铺关联route_key重复：{route_key}")
        if revision.isdigit() and route_version.isdigit():
            if int(route_version) > int(revision):
                raise ManualStoreRouteError(
                    f"手工单店铺关联{route_id}版本高于正式库版本。"
                )
        seen_ids.add(route_id)
        seen_keys.add(route_key)
        normalized.append(
            {
                "route_id": route_id,
                "route_key": route_key,
                "scope_id": scope_id,
                "product_code": target.product_code,
                "spec_code": target.spec_code,
                "store": store,
                "status": "active",
                "route_version": route_version,
                "confirmed_by": clean_identifier(source.get("confirmed_by")),
                "confirmed_at": clean_identifier(source.get("confirmed_at")),
                "evidence": clean_identifier(source.get("evidence"))
                or "飞书手工单商品店铺关联表",
            }
        )
    return sorted(normalized, key=lambda item: item["route_key"])
