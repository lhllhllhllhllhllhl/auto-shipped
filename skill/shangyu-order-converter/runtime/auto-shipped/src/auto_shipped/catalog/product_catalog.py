from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from .catalog_layers import CatalogLayer, build_effective_catalog
from .batch_mapping_replacement import validate_batch_mapping_replacement_document
from .product_candidates import (
    RankedProductCandidate,
    rank_alias_family_candidates,
    rank_product_candidates,
)
from .product_phrase_normalizer import (
    CanonicalSourceIdentity,
    ProductPhraseNormalizer,
)


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


def mapping_match_mode(identifier_type: str, source_spec: Any = "") -> str:
    """Describe the deterministic source key used by one mapping.

    The mode is derived instead of maintained as a second writable field, so it
    cannot drift away from ``identifier_type`` and ``source_spec``.
    """

    base = "name" if clean_identifier(identifier_type) == "product_name" else "code"
    return f"{base}_spec" if normalize_product_text(source_spec) else f"{base}_only"


@dataclass(frozen=True, slots=True)
class ProductRecord:
    product_code: str
    product_name: str
    spec_code: str
    spec_name: str
    weight_grams: float | None
    default_warehouse: str
    catalog_source_id: str = "guanyi_master"
    catalog_priority: int = 0
    catalog_source_ids: tuple[str, ...] = ("guanyi_master",)


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
    package_notice_code: str = ""
    package_notice: str = ""
    mapping_authority: str = ""
    reuse_scope: str = ""
    confidence_tier: str = ""
    candidate_evidence: tuple[RankedProductCandidate, ...] = ()
    canonical_identity: CanonicalSourceIdentity | None = None
    mapping_ids: tuple[str, ...] = ()
    review_warnings: tuple[dict[str, str], ...] = ()


class ProductCatalog:
    def __init__(
        self,
        records: list[ProductRecord],
        external_mappings: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
        source_policies: dict[str, dict[str, Any]] | None = None,
        package_semantics_registry: dict[str, Any] | None = None,
        batch_mapping_replacements: list[dict[str, Any]] | None = None,
        manual_store_routes: list[dict[str, Any]] | None = None,
        item_expansion_rules: list[dict[str, Any]] | None = None,
        phrase_normalizer: ProductPhraseNormalizer | None = None,
        mapping_revision: str = "",
        mapping_snapshot_sha256: str = "",
    ) -> None:
        self.records = records
        self.source_policies = source_policies or {}
        self.package_semantics_registry = package_semantics_registry or {}
        self.manual_store_routes = tuple(
            dict(route) for route in (manual_store_routes or [])
        )
        self.item_expansion_rules = tuple(
            dict(rule) for rule in (item_expansion_rules or [])
        )
        self.phrase_normalizer = phrase_normalizer or ProductPhraseNormalizer()
        self.mapping_revision = clean_identifier(mapping_revision)
        self.mapping_snapshot_sha256 = clean_identifier(mapping_snapshot_sha256)
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
        self._by_spec_name: dict[str, list[ProductRecord]] = {}
        for record in records:
            if record.product_code:
                self._by_product_code.setdefault(record.product_code, []).append(record)
            if record.spec_code:
                self._by_spec_code.setdefault(record.spec_code, []).append(record)
            normalized_name = normalize_product_text(record.product_name)
            if normalized_name:
                self._by_product_name.setdefault(normalized_name, []).append(record)
            normalized_spec_name = normalize_product_text(record.spec_name)
            if normalized_spec_name:
                self._by_spec_name.setdefault(normalized_spec_name, []).append(record)

        self._records_by_priority = [
            tuple(
                record
                for record in records
                if record.catalog_priority == priority
            )
            for priority in sorted(
                {record.catalog_priority for record in records}, reverse=True
            )
        ]

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
            if not source_value and not source_spec:
                continue
            key = (source_profile_id, identifier_type, source_value, source_spec)
            self._mappings.setdefault(key, []).append(mapping)

        self._batch_mapping_replacements: dict[
            tuple[str, str, str, str], list[dict[str, Any]]
        ] = {}
        for replacement in batch_mapping_replacements or []:
            source_profile_id = clean_identifier(
                replacement.get("source_profile_id") or "*"
            )
            identifier_type = clean_identifier(
                replacement.get("identifier_type") or "product_code"
            )
            source_value = self._normalize_source_value(
                identifier_type,
                replacement.get("source_value"),
            )
            source_spec = normalize_product_text(replacement.get("source_spec"))
            if not source_value and not source_spec:
                continue
            key = (source_profile_id, identifier_type, source_value, source_spec)
            self._batch_mapping_replacements.setdefault(key, []).append(replacement)

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
        preferred_catalog_paths: Iterable[str | Path] = (),
        policy_paths: Iterable[str | Path] = (),
        mapping_overlay_paths: Iterable[str | Path] = (),
        mapping_replacement_paths: Iterable[str | Path] = (),
    ) -> "ProductCatalog":
        preferred = [Path(value) for value in preferred_catalog_paths]
        layers = [
            CatalogLayer(
                source_id=f"business_preferred_{index}",
                path=path,
                priority=1000 - index,
            )
            for index, path in enumerate(preferred)
        ]
        layers.append(
            CatalogLayer(
                source_id="guanyi_master",
                path=Path(catalog_csv),
                priority=0,
                require_product_code=False,
            )
        )
        effective = build_effective_catalog(layers)
        records = [
            ProductRecord(
                product_code=row.product_code,
                product_name=row.product_name,
                spec_code=row.spec_code,
                spec_name=row.spec_name,
                weight_grams=row.weight_grams,
                default_warehouse=row.default_warehouse,
                catalog_source_id=row.catalog_source_id,
                catalog_priority=row.catalog_priority,
                catalog_source_ids=row.catalog_source_ids,
            )
            for row in effective.rows
        ]
        documents = [json.loads(Path(mappings_json).read_text(encoding="utf-8"))]
        package_semantics_registry: dict[str, Any] = {}
        phrase_normalizer: ProductPhraseNormalizer | None = None
        default_phrase_lexicon = (
            Path(__file__).resolve().parents[3]
            / "config/catalog/product_phrase_lexicon_v1.json"
        )
        if default_phrase_lexicon.is_file():
            phrase_normalizer = ProductPhraseNormalizer.from_file(
                default_phrase_lexicon
            )
        for policy_path in policy_paths:
            policy = Path(policy_path)
            if not policy.is_file():
                continue
            document = json.loads(policy.read_text(encoding="utf-8"))
            if document.get("registry_id") == "package_semantics_v1":
                package_semantics_registry = document
            elif document.get("registry_id") == "product_phrase_lexicon_v1":
                phrase_normalizer = ProductPhraseNormalizer(document)
        for overlay_path in mapping_overlay_paths:
            overlay = Path(overlay_path)
            if overlay.is_file():
                documents.append(json.loads(overlay.read_text(encoding="utf-8")))

        batch_mapping_replacements: list[dict[str, Any]] = []
        for replacement_path in mapping_replacement_paths:
            replacement_file = Path(replacement_path)
            if not replacement_file.is_file():
                continue
            replacement_document = json.loads(
                replacement_file.read_text(encoding="utf-8")
            )
            try:
                validated_replacements = (
                    validate_batch_mapping_replacement_document(replacement_document)
                )
            except ValueError as exc:
                raise ValueError(
                    f"当前批次映射替换记录格式不正确：{replacement_file}: {exc}"
                ) from exc
            batch_mapping_replacements.extend(validated_replacements)

        mapping_records: list[dict[str, Any]] = []
        manual_store_routes: list[dict[str, Any]] = []
        item_expansion_rules: list[dict[str, Any]] = []
        source_policies: dict[str, dict[str, Any]] = {}
        mapping_revision = ""
        mapping_snapshot_sha256 = ""
        for document in documents:
            if document.get("source") == "feishu_official_mapping_repository":
                mapping_revision = clean_identifier(document.get("mapping_revision"))
                mapping_snapshot_sha256 = clean_identifier(
                    document.get("snapshot_sha256")
                )
            source_policies.update(document.get("source_policies") or {})
            mapping_records.extend(
                cls._normalize_mapping_records(document.get("mappings") or {})
            )
            manual_store_routes.extend(
                dict(item) for item in document.get("manual_store_routes") or []
            )
            item_expansion_rules.extend(
                dict(item) for item in document.get("item_expansions") or []
            )
        return cls(
            records,
            mapping_records,
            source_policies,
            package_semantics_registry,
            batch_mapping_replacements,
            manual_store_routes,
            item_expansion_rules,
            phrase_normalizer,
            mapping_revision,
            mapping_snapshot_sha256,
        )

    def canonicalize_source_identity(
        self,
        *,
        source_item_text: Any = "",
        source_brand: Any = "",
        source_product_name: Any = "",
        source_spec: Any = "",
        unit: Any = "",
    ) -> CanonicalSourceIdentity:
        return self.phrase_normalizer.normalize(
            source_item_text=source_item_text,
            source_brand=source_brand,
            source_product_name=source_product_name,
            source_spec=source_spec,
            unit=unit,
        )

    def _mapping_canonical_identity(
        self,
        mapping: dict[str, Any],
    ) -> CanonicalSourceIdentity:
        canonical_product = clean_identifier(
            mapping.get("canonical_source_product")
        )
        if canonical_product:
            return self.canonicalize_source_identity(
                source_brand=mapping.get("canonical_source_brand"),
                source_product_name=canonical_product,
                source_spec=mapping.get("canonical_source_spec"),
            )
        return self.canonicalize_source_identity(
            source_product_name=mapping.get("source_value"),
            source_spec=mapping.get("source_spec"),
        )

    def _canonical_name_mapping_candidates(
        self,
        *,
        source_profile_id: str,
        source_item_text: Any,
        source_brand: Any,
        source_product_name: Any,
        source_spec: Any,
        unit: Any,
    ) -> tuple[CanonicalSourceIdentity, list[dict[str, Any]]]:
        identity = self.canonicalize_source_identity(
            source_item_text=source_item_text,
            source_brand=source_brand,
            source_product_name=source_product_name,
            source_spec=source_spec,
            unit=unit,
        )
        if not identity.product or any(
            issue.endswith("_ambiguous") for issue in identity.issues
        ):
            return identity, []
        profile = clean_identifier(source_profile_id)
        for candidate_profile in dict.fromkeys((profile, "*")):
            candidates = [
                mapping
                for mapping in self.mapping_records
                if clean_identifier(mapping.get("identifier_type"))
                == "product_name"
                and clean_identifier(
                    mapping.get("source_profile_id")
                    or mapping.get("source_profile")
                    or "*"
                )
                == candidate_profile
                and self._mapping_canonical_identity(mapping).signature
                == identity.signature
            ]
            if candidates:
                return identity, candidates
        return identity, []

    @staticmethod
    def _highest_priority_candidates(
        candidates: Iterable[ProductRecord],
    ) -> list[ProductRecord]:
        values = list(candidates)
        if not values:
            return []
        priority = max(record.catalog_priority for record in values)
        return [record for record in values if record.catalog_priority == priority]

    def shipping_forbidden_reason(self, product: ProductRecord) -> str:
        return self._forbidden_shipping_products.get(
            (product.product_code, product.spec_code),
            "",
        )

    def _unique_pair(self, product_code: str, spec_code: str) -> ProductRecord | None:
        candidates = self._highest_priority_candidates(
            record
            for record in self._by_product_code.get(product_code, [])
            if record.spec_code == spec_code
        )
        return candidates[0] if len(candidates) == 1 else None

    def has_unique_pair(self, product_code: str, spec_code: str) -> bool:
        return self._unique_pair(product_code, spec_code) is not None

    def variants_for_product_code(self, product_code: Any) -> tuple[ProductRecord, ...]:
        """Return unique Guanyi SKU candidates for one product code."""

        candidates = self._highest_priority_candidates(
            self._by_product_code.get(clean_identifier(product_code), [])
        )
        unique = {
            (record.product_code, record.spec_code): record
            for record in candidates
        }
        return tuple(
            unique[key]
            for key in sorted(unique)
        )

    def resolve_unique_target(
        self,
        product_code: Any,
        spec_code: Any = "",
    ) -> ProductRecord | None:
        """Resolve an explicit target pair or a genuinely single-variant product.

        A missing target spec is never guessed when a Guanyi product code owns
        multiple variants.
        """

        normalized_product = clean_identifier(product_code)
        normalized_spec = clean_identifier(spec_code)
        if normalized_spec:
            return self._unique_pair(normalized_product, normalized_spec)
        variants = self.variants_for_product_code(normalized_product)
        if len(variants) != 1:
            return None
        candidate = variants[0]
        return self._unique_pair(candidate.product_code, candidate.spec_code)

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
        if not normalized_value and not normalized_spec:
            return []
        profile = clean_identifier(source_profile_id) or "*"
        # A source-supplied specification is part of the identity.  Never fall
        # back to a code-only/name-only mapping when the exact composite key is
        # missing: doing so silently merges variants.  Blank-spec mappings are
        # still supported for source products that genuinely have no spec.
        keys = [(profile, identifier_type, normalized_value, normalized_spec)]
        if profile != "*":
            keys.append(("*", identifier_type, normalized_value, normalized_spec))
        for key in keys:
            mappings = self._mappings.get(key, [])
            if mappings:
                return mappings
        return []

    def _cross_source_exact_alias_resolution(
        self,
        *,
        source_profile_id: str,
        source_item_text: Any = "",
        source_brand: Any = "",
        source_product_name: Any,
        source_spec: Any = "",
        unit: Any = "",
    ) -> ProductResolution | None:
        """Return a read-only candidate learned from another source's exact alias.

        Source-scoped mappings remain authoritative only for their own source.
        An identical normalized product-name/spec pair from another source may
        suggest a target, but cannot become confirmed until the user promotes
        it to a global alias or confirms it for the current batch/source.
        """

        normalized_name = normalize_product_text(source_product_name)
        normalized_spec = normalize_product_text(source_spec)
        if not normalized_name:
            return None
        canonical_identity = self.canonicalize_source_identity(
            source_item_text=source_item_text,
            source_brand=source_brand,
            source_product_name=source_product_name,
            source_spec=source_spec,
            unit=unit,
        )
        current_forms = (
            {normalized_name + normalized_spec, normalized_spec + normalized_name}
            if normalized_spec
            else {normalized_name}
        )
        current_profile = clean_identifier(source_profile_id)
        targets: dict[tuple[str, str], ProductRecord] = {}
        for mapping in self.mapping_records:
            if clean_identifier(mapping.get("identifier_type")) != "product_name":
                continue
            mapping_profile = clean_identifier(
                mapping.get("source_profile_id")
                or mapping.get("source_profile")
                or "*"
            )
            if mapping_profile in {current_profile, "*"}:
                continue
            if (
                clean_identifier(mapping.get("mapping_authority"))
                != "feishu_official"
            ):
                continue
            if clean_identifier(mapping.get("reuse_scope")) != "future":
                continue
            mapping_name = self._normalize_source_value(
                "product_name", mapping.get("source_value")
            )
            mapping_spec = normalize_product_text(mapping.get("source_spec"))
            mapping_forms = (
                {mapping_name + mapping_spec, mapping_spec + mapping_name}
                if mapping_spec
                else {mapping_name}
            )
            mapping_identity = self._mapping_canonical_identity(mapping)
            raw_match = bool(current_forms.intersection(mapping_forms))
            canonical_match = (
                bool(canonical_identity.product)
                and not any(
                    issue.endswith("_ambiguous")
                    for issue in canonical_identity.issues
                )
                and mapping_identity.signature == canonical_identity.signature
            )
            if not raw_match and not canonical_match:
                continue
            target = self.resolve_unique_target(
                mapping.get("product_code"), mapping.get("spec_code")
            )
            if target is not None:
                targets[(target.product_code, target.spec_code)] = target
        if len(targets) == 1:
            product = next(iter(targets.values()))
            return ProductResolution(
                status="unconfirmed",
                external_sku=clean_identifier(source_product_name),
                product=product,
                confirmed=False,
                reason=(
                    "其他来源的飞书正式映射中存在完全相同的商品名称和规格；"
                    "本次仅作为跨来源候选，需确认仅本次、当前来源或所有来源沿用"
                ),
                match_method="cross_source_exact_alias_candidate",
                identifier_type="product_name",
                candidates=(product,),
                confidence_tier="preview_safe",
                canonical_identity=canonical_identity,
            )
        if targets:
            return ProductResolution(
                status="ambiguous",
                external_sku=clean_identifier(source_product_name),
                product=None,
                confirmed=False,
                reason="其他来源的同名正式别名指向多个管易商品，必须由用户选择",
                match_method="cross_source_exact_alias_candidates",
                identifier_type="product_name",
                candidates=tuple(targets.values()),
                canonical_identity=canonical_identity,
            )
        return None

    @staticmethod
    def _target_code_family_prefix(product_code: Any) -> str:
        """Return a conservative alphabetic SKU family prefix.

        Product families are inferred only from prefixes of at least four
        letters (for example ``KKAW``).  Short generic prefixes such as ``J``
        or ``SW`` are deliberately ignored because they group unrelated
        catalog products too broadly.
        """

        match = re.match(r"[A-Za-z]{4,}", clean_identifier(product_code))
        return match.group(0).casefold() if match else ""

    def _official_alias_family_prefixes(
        self,
        source_product_name: Any,
    ) -> tuple[str, ...]:
        """Find stable target families learned from repeated official aliases.

        One mapping cannot establish a product family.  At least two distinct
        official future mappings for the same normalized source product name
        must point to different SKUs with the same conservative code prefix.
        """

        normalized_name = normalize_product_text(source_product_name)
        if not normalized_name:
            return ()
        family_codes: dict[str, set[str]] = {}
        for mapping in self.mapping_records:
            if clean_identifier(mapping.get("identifier_type")) != "product_name":
                continue
            if clean_identifier(mapping.get("mapping_authority")) != "feishu_official":
                continue
            if clean_identifier(mapping.get("reuse_scope")) != "future":
                continue
            if self._normalize_source_value(
                "product_name", mapping.get("source_value")
            ) != normalized_name:
                continue
            product_code = clean_identifier(mapping.get("product_code"))
            spec_code = clean_identifier(mapping.get("spec_code"))
            if self.resolve_unique_target(product_code, spec_code) is None:
                continue
            prefix = self._target_code_family_prefix(product_code)
            if prefix:
                family_codes.setdefault(prefix, set()).add(product_code)
        return tuple(
            sorted(
                prefix
                for prefix, product_codes in family_codes.items()
                if len(product_codes) >= 2
            )
        )

    def _official_alias_family_resolution(
        self,
        *,
        source_product_name: Any,
        source_spec: Any,
    ) -> ProductResolution | None:
        """Suggest a new variant inside a family proven by official aliases."""

        prefixes = self._official_alias_family_prefixes(source_product_name)
        if len(prefixes) != 1 or not normalize_product_text(source_spec):
            return None
        prefix = prefixes[0]
        ranked_candidates: tuple[RankedProductCandidate, ...] = ()
        for layer_records in self._records_by_priority:
            family_records = [
                record
                for record in layer_records
                if record.product_code.casefold().startswith(prefix)
            ]
            if not family_records:
                continue
            ranked_candidates = rank_alias_family_candidates(
                family_records,
                source_spec=clean_identifier(source_spec),
                normalizer=normalize_product_text,
            )
            if ranked_candidates:
                break
        if not ranked_candidates:
            return None

        candidates = tuple(candidate.product for candidate in ranked_candidates)
        preview_safe = (
            len(ranked_candidates) == 1
            and ranked_candidates[0].confidence_tier == "preview_safe"
        )
        family_label = prefix.upper()
        if preview_safe:
            return ProductResolution(
                status="unconfirmed",
                external_sku=clean_identifier(source_product_name),
                product=candidates[0],
                confirmed=False,
                reason=(
                    "飞书正式映射中同一来源商品名称已有多个已确认SKU，"
                    f"共同形成{family_label}商品系列；当前规格在该系列内唯一完整命中。"
                    "本次只生成待确认预览，禁止自动写入正式映射"
                ),
                match_method="official_alias_family_spec_candidate",
                identifier_type="product_name",
                candidates=candidates,
                confidence_tier="preview_safe",
                candidate_evidence=ranked_candidates,
            )
        return ProductResolution(
            status="ambiguous",
            external_sku=clean_identifier(source_product_name),
            product=None,
            confirmed=False,
            reason=(
                "飞书正式映射已识别同一商品系列，"
                f"但当前规格在{family_label}系列内仍对应多个候选，必须由用户选择"
            ),
            match_method="official_alias_family_spec_candidates",
            identifier_type="product_name",
            candidates=candidates,
            confidence_tier="blocking",
            candidate_evidence=ranked_candidates,
        )

    def resolve_official_mapping_target(
        self,
        *,
        source_profile_id: str,
        identifier_type: str,
        source_value: Any,
        source_spec: Any = "",
    ) -> ProductRecord | None:
        """Return the unique active official target for one exact source key."""

        mappings = [
            mapping
            for mapping in self._mapping_candidates(
                source_profile_id,
                identifier_type,
                source_value,
                source_spec,
            )
            if clean_identifier(mapping.get("mapping_authority"))
            == "feishu_official"
            and clean_identifier(mapping.get("reuse_scope")) == "future"
        ]
        targets = {
            (target.product_code, target.spec_code): target
            for mapping in mappings
            if (
                target := self.resolve_unique_target(
                    mapping.get("product_code"), mapping.get("spec_code")
                )
            )
            is not None
        }
        return next(iter(targets.values())) if len(targets) == 1 else None

    def _resolve_batch_replacement(
        self,
        *,
        mappings: list[dict[str, Any]],
        replacements: list[dict[str, Any]],
        external_sku: str,
        identifier_type: str,
        source_fingerprint: str,
    ) -> ProductResolution | None:
        fingerprint = clean_identifier(source_fingerprint)
        applicable = [
            replacement
            for replacement in replacements
            if clean_identifier(replacement.get("scope")) == "current_batch"
            and clean_identifier(replacement.get("source_fingerprint")) == fingerprint
        ]
        # Old records are deliberately ignored for a different source file. This
        # makes the override expire naturally without mutating or deleting history.
        if not applicable:
            return None
        if not fingerprint:
            return None

        official_targets = {
            (product.product_code, product.spec_code): product
            for mapping in mappings
            if clean_identifier(mapping.get("mapping_authority"))
            == "feishu_official"
            and clean_identifier(mapping.get("reuse_scope")) == "future"
            and (
                product := self.resolve_unique_target(
                    mapping.get("product_code"), mapping.get("spec_code")
                )
            )
            is not None
        }
        if len(official_targets) != 1:
            return ProductResolution(
                status="unconfirmed",
                external_sku=external_sku,
                product=None,
                confirmed=False,
                reason="当前批次替换只允许替换一个唯一的飞书正式映射",
                match_method="current_batch_replacement_official_target_invalid",
                identifier_type=identifier_type,
            )
        official_pair = next(iter(official_targets))
        replacement_targets: dict[tuple[str, str], ProductRecord] = {}
        for replacement in applicable:
            expected_pair = (
                clean_identifier(replacement.get("expected_product_code")),
                clean_identifier(replacement.get("expected_spec_code")),
            )
            if expected_pair != official_pair:
                return ProductResolution(
                    status="unconfirmed",
                    external_sku=external_sku,
                    product=None,
                    confirmed=False,
                    reason="当前批次替换记录中的旧目标已与正式映射不同，请重新确认",
                    match_method="current_batch_replacement_stale_official_target",
                    identifier_type=identifier_type,
                )
            product = self.resolve_unique_target(
                replacement.get("replacement_product_code"),
                replacement.get("replacement_spec_code"),
            )
            if product is not None:
                replacement_targets[(product.product_code, product.spec_code)] = product
        if len(replacement_targets) != 1:
            return ProductResolution(
                status="ambiguous" if replacement_targets else "unmapped",
                external_sku=external_sku,
                product=None,
                confirmed=False,
                reason="当前批次替换记录存在多个目标，或替换目标已不在有效商品目录中",
                match_method="current_batch_replacement_target_invalid",
                identifier_type=identifier_type,
                candidates=tuple(replacement_targets.values()),
            )
        product = next(iter(replacement_targets.values()))
        return ProductResolution(
            status="confirmed",
            external_sku=external_sku,
            product=product,
            confirmed=True,
            reason="用户否决正式映射后确认的当前批次临时替换；未修改正式映射",
            match_method="current_batch_official_mapping_replacement",
            identifier_type=identifier_type,
        )

    def _composite_name_mapping_candidates(
        self,
        source_profile_id: str,
        source_product_name: Any,
    ) -> list[dict[str, Any]]:
        """Match an unsplit name against a published name+spec mapping.

        Text/OCR agents sometimes preserve a phrase such as ``橘色搓澡巾`` in
        ``source_product_name`` instead of splitting it into ``搓澡巾`` and
        ``橘色``.  This fallback is deliberately narrow: it only accepts an
        exact normalized concatenation of the two fields from an already
        published mapping.  It never uses fuzzy similarity and never runs when
        the source supplied a separate spec, because that spec is part of the
        source identity and must not be discarded.
        """

        normalized_name = normalize_product_text(source_product_name)
        if not normalized_name:
            return []
        profile = clean_identifier(source_profile_id) or "*"
        matches_by_profile: dict[str, list[dict[str, Any]]] = {
            profile: [],
            "*": [],
        }
        for mapping in self.mapping_records:
            if clean_identifier(mapping.get("identifier_type")) != "product_name":
                continue
            mapping_profile = clean_identifier(
                mapping.get("source_profile_id")
                or mapping.get("source_profile")
                or "*"
            )
            if mapping_profile not in matches_by_profile:
                continue
            source_value = normalize_product_text(mapping.get("source_value"))
            source_spec = normalize_product_text(mapping.get("source_spec"))
            if not source_value or not source_spec:
                continue
            if normalized_name not in {
                source_value + source_spec,
                source_spec + source_value,
            }:
                continue
            matches_by_profile[mapping_profile].append(mapping)
        if matches_by_profile[profile]:
            return matches_by_profile[profile]
        return matches_by_profile["*"] if profile != "*" else []

    def _resolve_mappings(
        self,
        mappings: list[dict[str, Any]],
        external_sku: str,
        identifier_type: str,
        match_method: str,
        source_fingerprint: str = "",
        source_profile_id: str = "",
    ) -> ProductResolution:
        replacements: list[dict[str, Any]] = []
        seen_replacement_ids: set[str] = set()
        for mapping in mappings:
            profile = clean_identifier(
                mapping.get("source_profile_id")
                or mapping.get("source_profile")
                or "*"
            )
            mapping_identifier_type = clean_identifier(
                mapping.get("identifier_type") or identifier_type
            )
            source_value = self._normalize_source_value(
                mapping_identifier_type,
                mapping.get("source_value"),
            )
            source_spec = normalize_product_text(mapping.get("source_spec"))
            replacement_profiles = [clean_identifier(source_profile_id), profile]
            for replacement_profile in dict.fromkeys(replacement_profiles):
                if not replacement_profile:
                    continue
                for replacement in self._batch_mapping_replacements.get(
                    (
                        replacement_profile,
                        mapping_identifier_type,
                        source_value,
                        source_spec,
                    ),
                    [],
                ):
                    replacement_id = clean_identifier(
                        replacement.get("replacement_id")
                    )
                    if replacement_id in seen_replacement_ids:
                        continue
                    seen_replacement_ids.add(replacement_id)
                    replacements.append(replacement)
        replacement_resolution = self._resolve_batch_replacement(
            mappings=mappings,
            replacements=replacements,
            external_sku=external_sku,
            identifier_type=identifier_type,
            source_fingerprint=source_fingerprint,
        )
        if replacement_resolution is not None:
            return replacement_resolution

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
        code_only_variant_binding = (
            identifier_type == "product_code"
            and all(
                not normalize_product_text(mapping.get("source_spec"))
                for mapping, _ in resolved
            )
            and len(self.variants_for_product_code(product.product_code)) > 1
        )
        future_reusable = any(
            clean_identifier(mapping.get("reuse_scope")) == "future"
            and clean_identifier(mapping.get("mapping_authority"))
            == "feishu_official"
            for mapping, _ in resolved
        )
        current_batch_confirmed = bool(source_fingerprint) and any(
            clean_identifier(mapping.get("reuse_scope")) == "current_batch"
            and clean_identifier(mapping.get("mapping_authority"))
            == "user_batch_confirmation"
            and clean_identifier(mapping.get("source_fingerprint"))
            == clean_identifier(source_fingerprint)
            for mapping, _ in resolved
        )
        current_batch_scoped = any(
            clean_identifier(mapping.get("reuse_scope")) == "current_batch"
            for mapping, _ in resolved
        )
        if current_batch_scoped and not (
            future_reusable or current_batch_confirmed
        ):
            return ProductResolution(
                status="unconfirmed",
                external_sku=external_sku,
                product=product,
                confirmed=False,
                reason="仅本次商品映射与当前来源文件指纹不一致，必须重新确认",
                match_method="current_batch_mapping_fingerprint_mismatch",
                identifier_type=identifier_type,
                candidates=tuple(unique_products.values()),
            )
        if code_only_variant_binding and not (
            future_reusable or current_batch_confirmed
        ):
            return ProductResolution(
                status="unconfirmed",
                external_sku=external_sku,
                product=product,
                confirmed=False,
                reason=(
                    "来源只有商品代码，而该管易商品代码包含多个规格；"
                    "必须由用户确认具体规格，并说明仅本次使用还是以后沿用"
                ),
                match_method="code_only_multi_spec_requires_confirmation",
                identifier_type=identifier_type,
                candidates=self.variants_for_product_code(product.product_code),
            )
        confirmed = all(bool(mapping.get("confirmed")) for mapping, _ in resolved)
        reasons = [str(mapping.get("reason") or "") for mapping, _ in resolved]
        mapping_authority = (
            "feishu_official"
            if future_reusable
            else "user_batch_confirmation"
            if current_batch_confirmed
            else ""
        )
        reuse_scope = (
            "future"
            if future_reusable
            else "current_batch"
            if current_batch_confirmed
            else ""
        )
        return ProductResolution(
            status="confirmed" if confirmed else "unconfirmed",
            external_sku=external_sku,
            product=product,
            confirmed=confirmed,
            reason=next((reason for reason in reasons if reason), ""),
            match_method=match_method,
            identifier_type=identifier_type,
            mapping_authority=mapping_authority,
            reuse_scope=reuse_scope,
            mapping_ids=tuple(
                sorted(
                    {
                        clean_identifier(mapping.get("mapping_id"))
                        for mapping, _ in resolved
                        if clean_identifier(mapping.get("mapping_id"))
                    }
                )
            ),
        )

    def _mapping_identity_guard_review(
        self,
        *,
        source_profile_id: str,
        identifier_type: str,
        source_value: Any,
        source_item_text: Any,
        source_brand: Any,
        source_product_name: Any,
        source_spec: Any,
        unit: Any,
        source_fingerprint: str,
        mappings: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        if source_fingerprint and any(
            clean_identifier(mapping.get("reuse_scope")) == "current_batch"
            and clean_identifier(mapping.get("mapping_authority"))
            == "user_batch_confirmation"
            and clean_identifier(mapping.get("source_fingerprint"))
            == clean_identifier(source_fingerprint)
            for mapping in mappings
        ):
            return None
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
        normalized_name = normalize_product_text(source_product_name)
        if not normalized_name:
            return None
        normalized_spec = normalize_product_text(source_spec)
        current_identity = self.canonicalize_source_identity(
            source_item_text=source_item_text,
            source_brand=source_brand,
            source_product_name=source_product_name,
            source_spec=source_spec,
            unit=unit,
        )
        reference_identities: list[CanonicalSourceIdentity] = [
            self._mapping_canonical_identity(mapping)
            for mapping in mappings
            if clean_identifier(mapping.get("canonical_source_product"))
        ]
        for guard in matching_guards:
            allowed_name_values = {
                clean_identifier(value)
                for value in guard.get("allowed_product_names") or []
                if clean_identifier(value)
            }
            allowed_spec_values = {
                clean_identifier(value)
                for value in guard.get("allowed_source_specs") or []
                if clean_identifier(value)
            }
            allowed_names = {
                normalize_product_text(value) for value in allowed_name_values
            }
            allowed_specs = {
                normalize_product_text(value) for value in allowed_spec_values
            }
            name_matches = not allowed_names or normalized_name in allowed_names
            spec_matches = not allowed_specs or normalized_spec in allowed_specs
            if name_matches and spec_matches:
                return None
            for allowed_name in allowed_name_values or {
                clean_identifier(source_product_name)
            }:
                for allowed_spec in allowed_spec_values or {
                    clean_identifier(source_spec)
                }:
                    reference_identities.append(
                        self.canonicalize_source_identity(
                            source_product_name=allowed_name,
                            source_spec=allowed_spec,
                        )
                    )

        ambiguous = any(issue.endswith("_ambiguous") for issue in current_identity.issues)
        if current_identity.product and not ambiguous:
            for reference_identity in reference_identities:
                reference_ambiguous = any(
                    issue.endswith("_ambiguous")
                    for issue in reference_identity.issues
                )
                if (
                    not reference_ambiguous
                    and current_identity.signature == reference_identity.signature
                ):
                    return {
                        "code": "SOURCE_NAME_VARIANT_ACCEPTED_FOR_CONFIRMED_CODE_MAPPING",
                        "severity": "notice_only",
                        "message": (
                            "来源编码/条码已唯一命中正式映射；来源商品名称只是已登记身份的"
                            "等义写法，已按该映射直接生成成品"
                        ),
                    }

        if not matching_guards and not reference_identities:
            if not guard_required:
                return None
            message = "来源编码命中正式映射，但该无规格映射缺少已登记的来源品名保护规则"
        else:
            message = "来源编码命中正式映射，但来源商品名称或规格与该映射的已确认身份不一致"
        return {
            "code": "SOURCE_NAME_DIFFERS_FROM_CONFIRMED_CODE_MAPPING",
            "severity": "review_required",
            "message": message,
        }

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
                spec_rules = self._matching_package_semantic_rules(
                    source_semantics.get("rules") or [],
                    normalized_name=normalized_name,
                    normalized_spec=normalized_spec,
                )
                semantic_confirmed = source_semantics.get("confirmed") is True
                if not spec_rules:
                    global_semantics = registry.get("global_semantics") or {}
                    spec_rules = self._matching_package_semantic_rules(
                        global_semantics.get("rules") or [],
                        normalized_name=normalized_name,
                        normalized_spec=normalized_spec,
                    )
                    semantic_confirmed = global_semantics.get("confirmed") is True
                if len(spec_rules) == 1 and semantic_confirmed:
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

    @staticmethod
    def _matching_package_semantic_rules(
        rules: list[dict[str, Any]],
        *,
        normalized_name: str,
        normalized_spec: str,
    ) -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        for rule in rules:
            exact_values = {
                normalize_product_text(value)
                for value in rule.get("source_spec_values") or []
                if normalize_product_text(value)
            }
            phrase_values = {
                normalize_product_text(value)
                for value in rule.get("source_phrase_values") or []
                if normalize_product_text(value)
            }
            exact_match = bool(normalized_spec and normalized_spec in exact_values)
            phrase_match = any(
                phrase in normalized_name or phrase in normalized_spec
                for phrase in phrase_values
            )
            if exact_match or phrase_match:
                matched.append(rule)
        return matched

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
        spec_rules = self._matching_package_semantic_rules(
            semantic.get("spec_rules") or [],
            normalized_name=normalized_name,
            normalized_spec=normalized_spec,
        )
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
        candidates = self._highest_priority_candidates(
            record
            for record in self.records
            if target_tokens
            and all(
                token in normalize_product_text(record.product_name)
                for token in target_tokens
            )
        )
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
                package_notice_code=clean_identifier(
                    spec_rules[0].get("notice_code")
                ),
                package_notice=clean_identifier(spec_rules[0].get("notice")),
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
        source_item_text: Any = "",
        source_brand: Any = "",
        source_product_name: Any = "",
        source_spec: Any = "",
        unit: Any = "",
        source_fingerprint: str = "",
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
                    f"confirmed_source_mapping_{mapping_match_mode(identifier_type, source_spec)}",
                    source_fingerprint,
                    source_profile_id,
                )
                guard_review = self._mapping_identity_guard_review(
                    source_profile_id=source_profile_id,
                    identifier_type=identifier_type,
                    source_value=key,
                    source_item_text=source_item_text,
                    source_brand=source_brand,
                    source_product_name=source_product_name,
                    source_spec=source_spec,
                    unit=unit,
                    source_fingerprint=source_fingerprint,
                    mappings=mappings,
                )
                if guard_review and resolved.product is not None:
                    return replace(
                        resolved,
                        reason=guard_review["message"],
                        review_warnings=(guard_review,),
                    )
                return resolved

        normalized_source_spec = normalize_product_text(source_spec)
        if normalized_source_spec and policy.get("allow_spec_only_mapping", False):
            mappings = self._mapping_candidates(
                source_profile_id,
                identifier_type,
                "",
                source_spec,
            )
            if mappings:
                return self._resolve_mappings(
                    mappings,
                    clean_identifier(source_spec),
                    identifier_type,
                    "confirmed_source_mapping_spec_only",
                    source_fingerprint,
                    source_profile_id,
                )

        if key:
            allow_direct = policy.get("allow_direct_catalog_identifier", False)
            direct = self._highest_priority_candidates(self._by_spec_code.get(key, []))
            if allow_direct and len(direct) == 1:
                return ProductResolution(
                    "confirmed",
                    key,
                    direct[0],
                    True,
                    "来源配置允许规格代码直连，且管易规格代码唯一",
                    "direct_spec_code",
                    identifier_type,
                )

            product_variants = list(self.variants_for_product_code(key))
            normalized_source_spec = normalize_product_text(source_spec)
            if normalized_source_spec:
                product_variants = [
                    product
                    for product in product_variants
                    if normalized_source_spec
                    in {
                        normalize_product_text(product.spec_code),
                        normalize_product_text(product.spec_name),
                    }
                ]
            if allow_direct and len(product_variants) == 1:
                return ProductResolution(
                    "confirmed",
                    key,
                    product_variants[0],
                    True,
                    "来源配置允许商品代码直连，且管易商品与规格唯一",
                    "direct_product_code",
                    identifier_type,
                )
            if len(product_variants) == 1:
                return ProductResolution(
                    "unconfirmed",
                    key,
                    product_variants[0],
                    False,
                    "来源代码与管易商品代码相同，但尚未登记为长期映射",
                    "direct_product_code_candidate",
                    identifier_type,
                    tuple(product_variants),
                )
            if product_variants:
                return ProductResolution(
                    "ambiguous",
                    key,
                    None,
                    False,
                    "来源商品代码对应管易多个规格，必须由用户确认具体规格",
                    "direct_product_code_candidates",
                    identifier_type,
                    tuple(product_variants),
                )

        # Packaging meaning is its own business authority.  Once no explicit
        # source code/identifier mapping has won, resolve registered package
        # semantics before name aliases so an old alias cannot silently turn
        # an eight-stick family pack into single-stick line items.
        semantic_resolution = self._resolve_semantic_catalog_match(
            source_profile_id=source_profile_id,
            source_product_name=source_product_name,
            source_spec=source_spec,
        )
        if semantic_resolution is not None:
            return semantic_resolution

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
                    f"confirmed_name_alias_{mapping_match_mode('product_name', source_spec)}",
                    source_fingerprint,
                    source_profile_id,
                )

            canonical_identity, canonical_mappings = (
                self._canonical_name_mapping_candidates(
                    source_profile_id=source_profile_id,
                    source_item_text=source_item_text,
                    source_brand=source_brand,
                    source_product_name=name,
                    source_spec=source_spec,
                    unit=unit,
                )
            )
            if canonical_mappings:
                resolution = self._resolve_mappings(
                    canonical_mappings,
                    key or name,
                    "product_name",
                    "confirmed_name_alias_canonical_identity",
                    source_fingerprint,
                    source_profile_id,
                )
                return replace(
                    resolution,
                    canonical_identity=canonical_identity,
                )

            cross_source_alias = self._cross_source_exact_alias_resolution(
                source_profile_id=source_profile_id,
                source_item_text=source_item_text,
                source_brand=source_brand,
                source_product_name=name,
                source_spec=source_spec,
                unit=unit,
            )
            if cross_source_alias is not None:
                return cross_source_alias

            if not normalize_product_text(source_spec):
                composite_mappings = self._composite_name_mapping_candidates(
                    source_profile_id,
                    name,
                )
                if composite_mappings:
                    return self._resolve_mappings(
                        composite_mappings,
                        key or name,
                        "product_name",
                        "confirmed_name_alias_composite_decomposition",
                        source_fingerprint,
                        source_profile_id,
                    )

            if policy.get("allow_exact_catalog_name", False):
                name_candidates = self._highest_priority_candidates(
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

            alias_family_resolution = self._official_alias_family_resolution(
                source_product_name=name,
                source_spec=source_spec,
            )
            if alias_family_resolution is not None:
                return alias_family_resolution

        if (
            name
            and policy.get("fuzzy_name_action")
            == "candidate_requires_user_confirmation"
        ):
            ranked_candidates: tuple[RankedProductCandidate, ...] = ()
            for layer_records in self._records_by_priority:
                ranked_candidates = rank_product_candidates(
                    layer_records,
                    source_name=name,
                    source_spec=source_spec,
                    normalizer=normalize_product_text,
                )
                if ranked_candidates:
                    break
            if ranked_candidates:
                candidates = tuple(candidate.product for candidate in ranked_candidates)
                preview_safe = (
                    len(ranked_candidates) == 1
                    and ranked_candidates[0].confidence_tier == "preview_safe"
                )
                if preview_safe:
                    reason = (
                        "商品名称仅相似命中一个候选，且名称、规格与关键语义属性"
                        "均达到待确认预览门槛；仍须由用户确认，禁止自动写入正式映射"
                    )
                else:
                    evidence_reason = ranked_candidates[0].confidence_reason
                    reason = (
                        "商品名称仅模糊命中候选，但候选证据不足以安全生成预览；"
                        f"{evidence_reason}，必须由用户确认具体管易商品"
                    )
                return ProductResolution(
                    status="unconfirmed" if preview_safe else "low_confidence",
                    external_sku=key or name,
                    product=candidates[0] if preview_safe else None,
                    confirmed=False,
                    reason=reason,
                    match_method="fuzzy_name_candidates",
                    identifier_type="product_name",
                    candidates=candidates,
                    confidence_tier="preview_safe" if preview_safe else "blocking",
                    candidate_evidence=ranked_candidates,
                )

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
