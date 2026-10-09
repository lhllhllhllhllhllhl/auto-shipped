from __future__ import annotations

import csv
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


def clean_identifier(value: Any) -> str:
    return str(value or "").replace("\t", "").strip()


def normalize_product_text(value: Any) -> str:
    """Normalize names/specs for deterministic exact matching, never fuzzy matching."""

    text = unicodedata.normalize("NFKC", clean_identifier(value)).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def _number(value: Any) -> float | None:
    text = clean_identifier(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ProductRecord:
    product_code: str
    product_name: str
    spec_code: str
    spec_name: str
    weight_grams: float | None
    default_warehouse: str


@dataclass(frozen=True, slots=True)
class ProductResolution:
    status: str
    external_sku: str
    product: ProductRecord | None
    confirmed: bool
    reason: str = ""
    match_method: str = ""
    identifier_type: str = ""
    candidates: tuple[ProductRecord, ...] = ()
    package_rule_id: str = ""
    quantity_strategy: str = "same_as_source"
    sticks_per_target_unit: int | None = None


class ProductCatalog:
    def __init__(
        self,
        records: list[ProductRecord],
        external_mappings: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
        source_policies: dict[str, dict[str, Any]] | None = None,
        package_semantics_registry: dict[str, Any] | None = None,
    ) -> None:
        self.records = records
        self.source_policies = source_policies or {}
        self.package_semantics_registry = package_semantics_registry or {}
        self._forbidden_shipping_products = {
            (
                clean_identifier(item.get("product_code")),
                clean_identifier(item.get("spec_code")),
            ): clean_identifier(item.get("reason")) or "该商品不允许进入普通发货订单"
            for item in self.package_semantics_registry.get(
                "forbidden_shipping_products", []
            )
            if clean_identifier(item.get("product_code"))
            and clean_identifier(item.get("spec_code"))
        }
        self._by_product_code: dict[str, list[ProductRecord]] = {}
        self._by_spec_code: dict[str, list[ProductRecord]] = {}
        self._by_product_name: dict[str, list[ProductRecord]] = {}
        for record in records:
            if record.product_code:
                self._by_product_code.setdefault(record.product_code, []).append(record)
            if record.spec_code:
                self._by_spec_code.setdefault(record.spec_code, []).append(record)
            normalized_name = normalize_product_text(record.product_name)
            if normalized_name:
                self._by_product_name.setdefault(normalized_name, []).append(record)

        self.mapping_records = self._normalize_mapping_records(external_mappings or {})
        self._mappings: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for mapping in self.mapping_records:
            source_profile_id = clean_identifier(
                mapping.get("source_profile_id") or mapping.get("source_profile") or "*"
            )
            identifier_type = clean_identifier(
                mapping.get("identifier_type") or "product_code"
            )
            source_value = self._normalize_source_value(
                identifier_type,
                mapping.get("source_value"),
            )
            source_spec = normalize_product_text(mapping.get("source_spec"))
            if not source_value:
                continue
            key = (source_profile_id, identifier_type, source_value, source_spec)
            self._mappings.setdefault(key, []).append(mapping)

    @staticmethod
    def _normalize_mapping_records(
        mappings: dict[str, dict[str, Any]] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if isinstance(mappings, list):
            return [dict(item) for item in mappings]
        return [
            {
                **dict(mapping),
                "source_value": external_key,
                "source_profile_id": mapping.get("source_profile_id")
                or mapping.get("source_profile")
                or "*",
                "identifier_type": mapping.get("identifier_type") or "product_code",
            }
            for external_key, mapping in mappings.items()
        ]

    @staticmethod
    def _normalize_source_value(identifier_type: str, value: Any) -> str:
        if identifier_type in {"product_name", "name_spec"}:
            return normalize_product_text(value)
        return clean_identifier(value)

    @classmethod
    def from_files(
        cls,
        catalog_csv: str | Path,
        mappings_json: str | Path,
        *,
        policy_paths: Iterable[str | Path] = (),
        mapping_overlay_paths: Iterable[str | Path] = (),
    ) -> "ProductCatalog":
        path = Path(catalog_csv)
        text = None
        for encoding in ("utf-8-sig", "gb18030", "utf-8"):
            try:
                text = path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError(f"无法识别商品文件编码: {path.name}")

        rows = csv.DictReader(text.splitlines())
        records = [
            ProductRecord(
                product_code=clean_identifier(row.get("商品代码")),
                product_name=clean_identifier(row.get("商品名称")),
                spec_code=clean_identifier(row.get("规格代码")),
                spec_name=clean_identifier(row.get("规格名称")),
                weight_grams=_number(row.get("重量")),
                default_warehouse=clean_identifier(row.get("默认仓库")),
            )
            for row in rows
        ]
        documents = [json.loads(Path(mappings_json).read_text(encoding="utf-8"))]
        package_semantics_registry: dict[str, Any] = {}
        for policy_path in policy_paths:
            policy = Path(policy_path)
            if not policy.is_file():
                continue
            document = json.loads(policy.read_text(encoding="utf-8"))
            if document.get("registry_id") == "package_semantics_v1":
                package_semantics_registry = document
        for overlay_path in mapping_overlay_paths:
            overlay = Path(overlay_path)
            if overlay.is_file():
                documents.append(json.loads(overlay.read_text(encoding="utf-8")))

        mapping_records: list[dict[str, Any]] = []
        source_policies: dict[str, dict[str, Any]] = {}
        for document in documents:
            source_policies.update(document.get("source_policies") or {})
            mapping_records.extend(
                cls._normalize_mapping_records(document.get("mappings") or {})
            )
        return cls(
            records,
            mapping_records,
            source_policies,
            package_semantics_registry,
        )

    def shipping_forbidden_reason(self, product: ProductRecord) -> str:
        return self._forbidden_shipping_products.get(
            (product.product_code, product.spec_code),
            "",
        )

    def _unique_pair(self, product_code: str, spec_code: str) -> ProductRecord | None:
        candidates = [
            record
            for record in self._by_product_code.get(product_code, [])
            if record.spec_code == spec_code
        ]
        return candidates[0] if len(candidates) == 1 else None

    def has_unique_pair(self, product_code: str, spec_code: str) -> bool:
        return self._unique_pair(product_code, spec_code) is not None

    def get_unique_pair(
        self,
        product_code: str,
        spec_code: str,
    ) -> ProductRecord | None:
        """Return one exact catalog record for a confirmed target code pair."""

        return self._unique_pair(
            clean_identifier(product_code),
            clean_identifier(spec_code),
        )

    def _mapping_candidates(
        self,
        source_profile_id: str,
        identifier_type: str,
        source_value: Any,
        source_spec: Any = "",
    ) -> list[dict[str, Any]]:
        normalized_value = self._normalize_source_value(identifier_type, source_value)
        normalized_spec = normalize_product_text(source_spec)
        if not normalized_value:
            return []
        profile = clean_identifier(source_profile_id) or "*"
        keys = [
            (profile, identifier_type, normalized_value, normalized_spec),
            (profile, identifier_type, normalized_value, ""),
        ]
        if profile != "*":
            keys.extend(
                [
                    ("*", identifier_type, normalized_value, normalized_spec),
                    ("*", identifier_type, normalized_value, ""),
                ]
            )
        for key in keys:
            mappings = self._mappings.get(key, [])
            if mappings:
                return mappings
        return []

    def _resolve_mappings(
        self,
        mappings: list[dict[str, Any]],
        external_sku: str,
        identifier_type: str,
        match_method: str,
    ) -> ProductResolution:
        resolved: list[tuple[dict[str, Any], ProductRecord]] = []
        for mapping in mappings:
            product = self._unique_pair(
                clean_identifier(mapping.get("product_code")),
                clean_identifier(mapping.get("spec_code")),
            )
            if product is not None:
                resolved.append((mapping, product))
        unique_products = {
            (product.product_code, product.spec_code): product
            for _, product in resolved
        }
        if len(unique_products) != 1:
            return ProductResolution(
                status="ambiguous" if unique_products else "unmapped",
                external_sku=external_sku,
                product=None,
                confirmed=False,
                reason=(
                    "同一来源标识存在多个商品映射"
                    if unique_products
                    else "外部映射指向的商品代码与规格代码在商品主数据中不是唯一组合"
                ),
                match_method=match_method,
                identifier_type=identifier_type,
                candidates=tuple(unique_products.values()),
            )
        product = next(iter(unique_products.values()))
        confirmed = all(bool(mapping.get("confirmed")) for mapping, _ in resolved)
        reasons = [str(mapping.get("reason") or "") for mapping, _ in resolved]
        return ProductResolution(
            status="confirmed" if confirmed else "unconfirmed",
            external_sku=external_sku,
            product=product,
            confirmed=confirmed,
            reason=next((reason for reason in reasons if reason), ""),
            match_method=match_method,
            identifier_type=identifier_type,
        )

    def _mapping_identity_guard_reason(
        self,
        *,
        source_profile_id: str,
        identifier_type: str,
        source_value: Any,
        source_product_name: Any,
        source_spec: Any,
        mappings: list[dict[str, Any]],
    ) -> str:
        policy = self.source_policies.get(source_profile_id) or {}
        normalized_value = self._normalize_source_value(identifier_type, source_value)
        matching_guards = [
            guard
            for guard in policy.get("mapping_identity_guards") or []
            if clean_identifier(guard.get("identifier_type") or "product_code")
            == identifier_type
            and self._normalize_source_value(
                identifier_type,
                guard.get("source_value"),
            )
            == normalized_value
        ]
        mapping_has_source_spec = any(
            normalize_product_text(mapping.get("source_spec")) for mapping in mappings
        )
        guard_required = bool(
            policy.get("require_identity_guard_for_blank_source_spec", False)
            and not mapping_has_source_spec
        )
        if not matching_guards:
            return (
                "来源编码命中正式映射，但该无规格映射缺少已登记的来源品名保护规则"
                if guard_required
                else ""
            )

        normalized_name = normalize_product_text(source_product_name)
        normalized_spec = normalize_product_text(source_spec)
        for guard in matching_guards:
            allowed_names = {
                normalize_product_text(value)
                for value in guard.get("allowed_product_names") or []
                if normalize_product_text(value)
            }
            allowed_specs = {
                normalize_product_text(value)
                for value in guard.get("allowed_source_specs") or []
                if normalize_product_text(value)
            }
            name_matches = not allowed_names or normalized_name in allowed_names
            spec_matches = not allowed_specs or normalized_spec in allowed_specs
            if name_matches and spec_matches:
                return ""
        return "来源编码命中正式映射，但来源商品名称或规格与该映射的已确认身份不一致"

    def _resolve_semantic_catalog_match(
        self,
        *,
        source_profile_id: str,
        source_product_name: Any,
        source_spec: Any,
    ) -> ProductResolution | None:
        registry = self.package_semantics_registry
        if registry:
            normalized_name = normalize_product_text(source_product_name)
            normalized_spec = normalize_product_text(source_spec)
            family_rules = [
                rule
                for rule in registry.get("product_family_rules") or []
                if any(
                    normalize_product_text(token) in normalized_name
                    for token in rule.get("source_name_contains_any") or []
                    if normalize_product_text(token)
                )
            ]
            if len(family_rules) == 1:
                if "裸棒" in normalized_name or normalize_product_text("裸棒") in normalized_spec:
                    return ProductResolution(
                        status="unconfirmed",
                        external_sku=f"{clean_identifier(source_product_name)} / {clean_identifier(source_spec)}",
                        product=None,
                        confirmed=False,
                        reason="裸棒仅用于试吃，当前普通发货流程已禁用。请确认改用哪个正式发货商品。",
                        match_method="shipping_expression_forbidden",
                        identifier_type="product_name_spec",
                    )
                source_semantics = (
                    registry.get("source_semantics", {}).get(source_profile_id) or {}
                )
                spec_rules = [
                    rule
                    for rule in source_semantics.get("rules") or []
                    if normalized_spec
                    in {
                        normalize_product_text(value)
                        for value in rule.get("source_spec_values") or []
                        if normalize_product_text(value)
                    }
                ]
                if len(spec_rules) == 1 and source_semantics.get("confirmed") is True:
                    semantic = {
                        "strategy": "configured_name_tokens_and_source_spec",
                        "confirmed": True,
                        "name_rules": family_rules,
                        "spec_rules": spec_rules,
                    }
                else:
                    tracked_units = {
                        normalize_product_text(value)
                        for value in registry.get("tracked_source_units") or []
                        if normalize_product_text(value)
                    }
                    if normalized_spec and any(
                        tracked in normalized_spec for tracked in tracked_units
                    ):
                        candidate = registry.get("default_candidate") or {}
                        candidate_text = ""
                        if normalized_spec == normalize_product_text(
                            candidate.get("source_unit")
                        ):
                            candidate_text = "；当前全局候选是彩袋单棒装且目标数量等于来源根数"
                        return ProductResolution(
                            status="unconfirmed",
                            external_sku=f"{clean_identifier(source_product_name)} / {clean_identifier(source_spec)}",
                            product=None,
                            confirmed=False,
                            reason=(
                                f"来源配置 {source_profile_id or '未登记来源'} 尚未登记包装表达“"
                                f"{clean_identifier(source_spec)}”的业务含义{candidate_text}。"
                            ),
                            match_method="package_semantics_unregistered",
                            identifier_type="product_name_spec",
                        )
                    semantic = {}
                if semantic:
                    return self._resolve_semantic_rules(
                        semantic=semantic,
                        source_product_name=source_product_name,
                        source_spec=source_spec,
                    )

        policy = self.source_policies.get(source_profile_id) or {}
        semantic = policy.get("semantic_catalog_match") or {}
        if not semantic:
            return None
        strategy = str(semantic.get("strategy") or "")
        if strategy != "configured_name_tokens_and_source_spec":
            return ProductResolution(
                status="unmapped",
                external_sku=clean_identifier(source_product_name),
                product=None,
                confirmed=False,
                reason=f"未知语义商品匹配策略：{strategy or '未配置'}",
                match_method="semantic_catalog_rule_invalid",
                identifier_type="product_name",
            )
        if semantic.get("confirmed") is not True:
            return ProductResolution(
                status="unconfirmed",
                external_sku=clean_identifier(source_product_name),
                product=None,
                confirmed=False,
                reason="语义商品匹配规则尚未确认",
                match_method="semantic_catalog_rule_unconfirmed",
                identifier_type="product_name",
            )

        return self._resolve_semantic_rules(
            semantic=semantic,
            source_product_name=source_product_name,
            source_spec=source_spec,
        )

    def _resolve_semantic_rules(
        self,
        *,
        semantic: dict[str, Any],
        source_product_name: Any,
        source_spec: Any,
    ) -> ProductResolution | None:
        normalized_name = normalize_product_text(source_product_name)
        normalized_spec = normalize_product_text(source_spec)
        name_rules = [
            rule
            for rule in semantic.get("name_rules") or []
            if any(
                normalize_product_text(token) in normalized_name
                for token in rule.get("source_name_contains_any") or []
                if normalize_product_text(token)
            )
        ]
        spec_rules = [
            rule
            for rule in semantic.get("spec_rules") or []
            if normalized_spec
            in {
                normalize_product_text(value)
                for value in rule.get("source_spec_values") or []
                if normalize_product_text(value)
            }
        ]
        if len(name_rules) != 1 or len(spec_rules) != 1:
            return None
        target_tokens = [
            normalize_product_text(value)
            for value in (
                list(name_rules[0].get("target_name_contains_all") or [])
                + list(spec_rules[0].get("target_name_contains_all") or [])
            )
            if normalize_product_text(value)
        ]
        candidates = [
            record
            for record in self.records
            if target_tokens
            and all(
                token in normalize_product_text(record.product_name)
                for token in target_tokens
            )
        ]
        unique_candidates = {
            (record.product_code, record.spec_code): record for record in candidates
        }
        label = f"{clean_identifier(source_product_name)} / {clean_identifier(source_spec)}"
        if len(unique_candidates) == 1:
            product = next(iter(unique_candidates.values()))
            return ProductResolution(
                status="confirmed",
                external_sku=label,
                product=product,
                confirmed=True,
                reason="来源品类和包装单位按已确认规则在管易商品资料中唯一命中",
                match_method="semantic_catalog_exact_unique",
                identifier_type="product_name_spec",
                package_rule_id=clean_identifier(spec_rules[0].get("rule_id")),
                quantity_strategy=clean_identifier(
                    spec_rules[0].get("quantity_strategy")
                )
                or "same_as_source",
                sticks_per_target_unit=(
                    int(spec_rules[0]["sticks_per_target_unit"])
                    if spec_rules[0].get("sticks_per_target_unit") is not None
                    else None
                ),
            )
        if unique_candidates:
            return ProductResolution(
                status="ambiguous",
                external_sku=label,
                product=None,
                confirmed=False,
                reason="来源品类和包装单位命中多个管易商品",
                match_method="semantic_catalog_exact_unique",
                identifier_type="product_name_spec",
                candidates=tuple(unique_candidates.values()),
            )
        return ProductResolution(
            status="unmapped",
            external_sku=label,
            product=None,
            confirmed=False,
            reason="来源品类和包装单位没有在管易商品资料中找到唯一商品",
            match_method="semantic_catalog_exact_unique",
            identifier_type="product_name_spec",
        )

    def resolve(
        self,
        external_sku: Any,
        *,
        source_profile_id: str = "",
        source_product_name: Any = "",
        source_spec: Any = "",
    ) -> ProductResolution:
        key = clean_identifier(external_sku)
        policy = self.source_policies.get(source_profile_id) or {}
        identifier_type = str(policy.get("source_identifier_type") or "product_code")

        if key:
            mappings = self._mapping_candidates(
                source_profile_id,
                identifier_type,
                key,
                source_spec,
            )
            if mappings:
                resolved = self._resolve_mappings(
                    mappings,
                    key,
                    identifier_type,
                    "confirmed_source_mapping",
                )
                guard_reason = self._mapping_identity_guard_reason(
                    source_profile_id=source_profile_id,
                    identifier_type=identifier_type,
                    source_value=key,
                    source_product_name=source_product_name,
                    source_spec=source_spec,
                    mappings=mappings,
                )
                if guard_reason and resolved.product is not None:
                    return ProductResolution(
                        status="unconfirmed",
                        external_sku=resolved.external_sku,
                        product=resolved.product,
                        confirmed=False,
                        reason=guard_reason,
                        match_method="source_mapping_identity_guard",
                        identifier_type=resolved.identifier_type,
                        candidates=resolved.candidates,
                    )
                return resolved

            if policy.get("allow_direct_catalog_identifier", False):
                direct = self._by_spec_code.get(key, [])
                if len(direct) == 1:
                    return ProductResolution(
                        "confirmed",
                        key,
                        direct[0],
                        True,
                        "来源配置允许规格代码直连，且管易规格代码唯一",
                        "direct_spec_code",
                        identifier_type,
                    )
                direct = self._by_product_code.get(key, [])
                if len(direct) == 1:
                    return ProductResolution(
                        "confirmed",
                        key,
                        direct[0],
                        True,
                        "来源配置允许商品代码直连，且管易商品代码唯一",
                        "direct_product_code",
                        identifier_type,
                    )

        name = clean_identifier(source_product_name) or ("" if key else key)
        if not name and not key:
            name = clean_identifier(external_sku)
        if name:
            alias_mappings = self._mapping_candidates(
                source_profile_id,
                "product_name",
                name,
                source_spec,
            )
            if alias_mappings:
                return self._resolve_mappings(
                    alias_mappings,
                    key or name,
                    "product_name",
                    "confirmed_name_alias",
                )

            if policy.get("allow_exact_catalog_name", False):
                name_candidates = list(
                    self._by_product_name.get(normalize_product_text(name), [])
                )
                normalized_spec = normalize_product_text(source_spec)
                if normalized_spec:
                    name_candidates = [
                        product
                        for product in name_candidates
                        if normalized_spec
                        in {
                            normalize_product_text(product.spec_name),
                            normalize_product_text(product.spec_code),
                        }
                    ]
                unique_candidates = {
                    (product.product_code, product.spec_code): product
                    for product in name_candidates
                }
                if len(unique_candidates) == 1:
                    product = next(iter(unique_candidates.values()))
                    return ProductResolution(
                        "confirmed",
                        key or name,
                        product,
                        True,
                        "标准化商品名称与管易商品资料唯一精确匹配",
                        "exact_catalog_name",
                        "product_name",
                    )
                if unique_candidates:
                    return ProductResolution(
                        "ambiguous",
                        key or name,
                        None,
                        False,
                        "商品名称精确匹配到多个管易规格，请补充规格或确认商品",
                        "exact_catalog_name",
                        "product_name",
                        tuple(unique_candidates.values()),
                    )

        semantic_resolution = self._resolve_semantic_catalog_match(
            source_profile_id=source_profile_id,
            source_product_name=source_product_name,
            source_spec=source_spec,
        )
        if semantic_resolution is not None:
            return semantic_resolution

        display = key or name
        return ProductResolution(
            "unmapped",
            display,
            None,
            False,
            "当前公司的已确认映射、名称别名和唯一精确商品名称均未命中",
            "no_match",
            identifier_type,
        )
