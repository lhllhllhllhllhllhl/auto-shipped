from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from auto_shipped.catalog import ProductCatalog, clean_identifier


ALLOWED_EXPANSION_IDENTIFIER_TYPES = {
    "product_code",
    "barcode",
    "product_name",
}


class ItemExpansionRegistryError(ValueError):
    """Raised when a governed one-to-many item expansion is invalid."""


def build_item_expansion_rule_key(
    *,
    company_id: Any,
    source_profile_id: Any,
    identifier_type: Any,
    source_value: Any,
    source_spec: Any = "",
    target_platform: Any = "guanyi",
) -> str:
    values = (
        clean_identifier(company_id),
        clean_identifier(source_profile_id),
        clean_identifier(identifier_type),
        clean_identifier(source_value),
        clean_identifier(source_spec),
        clean_identifier(target_platform),
    )
    if not values[0] or not values[1]:
        raise ItemExpansionRegistryError(
            "组合规则必须包含company_id和source_profile_id。"
        )
    if values[2] not in ALLOWED_EXPANSION_IDENTIFIER_TYPES:
        raise ItemExpansionRegistryError(
            f"组合规则使用未知identifier_type：{values[2] or '<empty>'}"
        )
    if not values[3] and not values[4]:
        raise ItemExpansionRegistryError(
            "组合规则的source_value和source_spec不能同时为空。"
        )
    if values[5] != "guanyi":
        raise ItemExpansionRegistryError("组合规则当前只支持guanyi目标平台。")
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    return f"item-expansion-{digest[:24]}"


@dataclass(frozen=True, slots=True)
class ItemExpansionComponentRecord:
    component_id: str
    rule_id: str
    rule_key: str
    company_id: str
    source_profile_id: str
    identifier_type: str
    source_value: str
    source_spec: str
    target_platform: str
    component_index: str
    product_code: str
    spec_code: str
    quantity_multiplier: str
    status: str
    rule_version: str
    confirmed_by: str
    confirmed_at: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


ITEM_EXPANSION_HEADERS = tuple(
    field.name for field in ItemExpansionComponentRecord.__dataclass_fields__.values()
)


def _canonical_multiplier(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ItemExpansionRegistryError("组合商品数量倍数必须是正数。") from exc
    if number <= 0:
        raise ItemExpansionRegistryError("组合商品数量倍数必须是正数。")
    return str(int(number)) if number.is_integer() else format(number, ".15g")


def normalize_item_expansion_rules(
    records: Iterable[Mapping[str, Any]],
    *,
    catalog: ProductCatalog,
    repository_revision: str,
) -> list[dict[str, Any]]:
    """Validate component rows and return one normalized rule per source key."""

    revision = clean_identifier(repository_revision)
    if not revision:
        raise ItemExpansionRegistryError("正式库缺少mapping_revision。")

    active_rows: list[dict[str, Any]] = []
    seen_component_ids: set[str] = set()
    for source in records:
        status = clean_identifier(source.get("status"))
        if status == "inactive":
            continue
        if status != "active":
            raise ItemExpansionRegistryError(
                f"组合商品组件{source.get('component_id')!r}状态不是active/inactive。"
            )
        component_id = clean_identifier(source.get("component_id"))
        rule_id = clean_identifier(source.get("rule_id"))
        company_id = clean_identifier(source.get("company_id"))
        source_profile_id = clean_identifier(source.get("source_profile_id"))
        identifier_type = clean_identifier(source.get("identifier_type"))
        source_value = clean_identifier(source.get("source_value"))
        source_spec = clean_identifier(source.get("source_spec"))
        target_platform = clean_identifier(source.get("target_platform"))
        product_code = clean_identifier(source.get("product_code"))
        spec_code = clean_identifier(source.get("spec_code"))
        rule_version = clean_identifier(source.get("rule_version"))
        required = {
            "component_id": component_id,
            "rule_id": rule_id,
            "company_id": company_id,
            "source_profile_id": source_profile_id,
            "identifier_type": identifier_type,
            "target_platform": target_platform,
            "product_code": product_code,
            "rule_version": rule_version,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ItemExpansionRegistryError(
                f"组合商品组件{component_id or '<unknown>'}缺少字段：{', '.join(missing)}"
            )
        if component_id in seen_component_ids:
            raise ItemExpansionRegistryError(
                f"组合商品component_id重复：{component_id}"
            )
        seen_component_ids.add(component_id)
        if not source_value and not source_spec:
            raise ItemExpansionRegistryError(
                f"组合商品组件{component_id}的来源标识和来源规格不能同时为空。"
            )
        try:
            component_index = int(clean_identifier(source.get("component_index")))
        except ValueError as exc:
            raise ItemExpansionRegistryError(
                f"组合商品组件{component_id}的component_index不是正整数。"
            ) from exc
        if component_index <= 0:
            raise ItemExpansionRegistryError(
                f"组合商品组件{component_id}的component_index不是正整数。"
            )
        multiplier = _canonical_multiplier(source.get("quantity_multiplier"))
        target = catalog.resolve_unique_target(product_code, spec_code)
        if target is None:
            raise ItemExpansionRegistryError(
                f"组合商品组件{component_id}的管易商品/规格不存在或不唯一。"
            )
        expected_key = build_item_expansion_rule_key(
            company_id=company_id,
            source_profile_id=source_profile_id,
            identifier_type=identifier_type,
            source_value=source_value,
            source_spec=source_spec,
            target_platform=target_platform,
        )
        rule_key = clean_identifier(source.get("rule_key"))
        if rule_key != expected_key:
            raise ItemExpansionRegistryError(
                f"组合商品组件{component_id}的rule_key校验失败。"
            )
        if revision.isdigit() and rule_version.isdigit():
            if int(rule_version) > int(revision):
                raise ItemExpansionRegistryError(
                    f"组合商品组件{component_id}版本高于正式库版本。"
                )
        active_rows.append(
            {
                "component_id": component_id,
                "rule_id": rule_id,
                "rule_key": rule_key,
                "company_id": company_id,
                "source_profile_id": source_profile_id,
                "identifier_type": identifier_type,
                "source_value": source_value,
                "source_spec": source_spec,
                "target_platform": target_platform,
                "component_index": component_index,
                "product_code": target.product_code,
                "spec_code": target.spec_code,
                "quantity_multiplier": multiplier,
                "status": "active",
                "rule_version": rule_version,
                "confirmed_by": clean_identifier(source.get("confirmed_by")),
                "confirmed_at": clean_identifier(source.get("confirmed_at")),
                "evidence": clean_identifier(source.get("evidence"))
                or "飞书组合商品规则表",
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in active_rows:
        grouped.setdefault(row["rule_key"], []).append(row)

    normalized: list[dict[str, Any]] = []
    seen_rule_ids: set[str] = set()
    identity_fields = (
        "rule_id",
        "company_id",
        "source_profile_id",
        "identifier_type",
        "source_value",
        "source_spec",
        "target_platform",
        "rule_version",
    )
    for rule_key, rows in grouped.items():
        rows.sort(key=lambda item: item["component_index"])
        first = rows[0]
        for row in rows[1:]:
            if any(row[field] != first[field] for field in identity_fields):
                raise ItemExpansionRegistryError(
                    f"组合规则{rule_key}的组件行身份字段不一致。"
                )
        rule_id = first["rule_id"]
        if rule_id in seen_rule_ids:
            raise ItemExpansionRegistryError(f"组合商品rule_id重复：{rule_id}")
        seen_rule_ids.add(rule_id)
        expected_indices = list(range(1, len(rows) + 1))
        actual_indices = [row["component_index"] for row in rows]
        if actual_indices != expected_indices:
            raise ItemExpansionRegistryError(
                f"组合规则{rule_id}的component_index必须从1连续排列。"
            )
        if len(rows) < 2:
            raise ItemExpansionRegistryError(
                f"组合规则{rule_id}至少需要两个目标商品。"
            )
        target_pairs = [
            (row["product_code"], row["spec_code"])
            for row in rows
        ]
        if len(set(target_pairs)) != len(target_pairs):
            raise ItemExpansionRegistryError(
                f"组合规则{rule_id}包含重复目标商品，请合并数量倍数。"
            )
        normalized.append(
            {
                "rule_id": rule_id,
                "rule_key": rule_key,
                "company_id": first["company_id"],
                "source_profile_id": first["source_profile_id"],
                "identifier_type": first["identifier_type"],
                "source_value": first["source_value"],
                "source_spec": first["source_spec"],
                "target_platform": first["target_platform"],
                "status": "active",
                "confirmed": True,
                "rule_version": first["rule_version"],
                "confirmed_by": first["confirmed_by"],
                "confirmed_at": first["confirmed_at"],
                "evidence": first["evidence"],
                "reuse_scope": "future",
                "mapping_authority": "feishu_official",
                "components": [
                    {
                        "component_index": row["component_index"],
                        "product_code": row["product_code"],
                        "spec_code": row["spec_code"],
                        "quantity_multiplier": float(row["quantity_multiplier"]),
                    }
                    for row in rows
                ],
            }
        )
    return sorted(normalized, key=lambda item: item["rule_key"])
