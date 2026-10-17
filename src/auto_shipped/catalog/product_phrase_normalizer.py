from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean(value)).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


@dataclass(frozen=True, slots=True)
class CanonicalSourceIdentity:
    brand: str
    product: str
    spec: str
    schema_version: str
    residual: str = ""
    issues: tuple[str, ...] = ()

    @property
    def signature(self) -> tuple[str, str, str, str]:
        return (self.schema_version, self.brand, self.product, self.spec)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = list(self.issues)
        return payload


@dataclass(frozen=True, slots=True)
class _AliasValue:
    canonical: str
    aliases: tuple[str, ...]


class ProductPhraseNormalizer:
    """Build a deterministic source identity without choosing a target SKU.

    The normalizer deliberately knows language aliases only.  It never stores
    or infers Guanyi product codes, and it never changes quantity or unit.
    """

    def __init__(self, registry: Mapping[str, Any] | None = None) -> None:
        document = dict(registry or {})
        self.registry_id = _clean(document.get("registry_id"))
        self.schema_version = (
            _clean(document.get("canonical_schema_version")) or "1.0"
        )
        self.brands = self._alias_values(document.get("brand_aliases") or [])
        self.subjects = self._alias_values(document.get("product_subjects") or [])
        self.spec_dimensions: tuple[tuple[str, tuple[_AliasValue, ...]], ...] = tuple(
            (
                _clean(dimension.get("dimension")),
                self._alias_values(dimension.get("values") or []),
            )
            for dimension in document.get("specification_dimensions") or []
            if isinstance(dimension, Mapping)
        )
        self.quantity_unit_patterns = tuple(
            re.compile(str(pattern), flags=re.IGNORECASE)
            for pattern in document.get("quantity_unit_patterns") or []
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "ProductPhraseNormalizer":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if document.get("registry_id") != "product_phrase_lexicon_v1":
            raise ValueError("商品短语词典registry_id不正确。")
        if document.get("status") != "active":
            raise ValueError("商品短语词典不是active。")
        return cls(document)

    @staticmethod
    def _alias_values(values: Iterable[Any]) -> tuple[_AliasValue, ...]:
        parsed: list[_AliasValue] = []
        for raw in values:
            if not isinstance(raw, Mapping):
                continue
            canonical = _normalized(raw.get("canonical"))
            aliases = {
                _normalized(alias)
                for alias in raw.get("aliases") or []
                if _normalized(alias)
            }
            if canonical:
                aliases.add(canonical)
            if canonical and aliases:
                parsed.append(
                    _AliasValue(
                        canonical=canonical,
                        aliases=tuple(sorted(aliases, key=lambda item: (-len(item), item))),
                    )
                )
        return tuple(parsed)

    @staticmethod
    def _matches(text: str, values: Iterable[_AliasValue]) -> list[tuple[_AliasValue, str]]:
        matches: list[tuple[_AliasValue, str]] = []
        for value in values:
            alias = next((alias for alias in value.aliases if alias in text), "")
            if alias:
                matches.append((value, alias))
        matches.sort(key=lambda item: (-len(item[1]), item[0].canonical))
        return matches

    @staticmethod
    def _remove_alias(text: str, alias: str) -> str:
        return text.replace(alias, "") if alias else text

    def normalize(
        self,
        *,
        source_item_text: Any = "",
        source_brand: Any = "",
        source_product_name: Any = "",
        source_spec: Any = "",
        unit: Any = "",
    ) -> CanonicalSourceIdentity:
        raw_text = _normalized(source_item_text)
        normalized_brand = _normalized(source_brand)
        normalized_name = _normalized(source_product_name)
        normalized_spec = _normalized(source_spec)
        normalized_unit = _normalized(unit)
        # The original phrase already contains the Agent split.  Do not append
        # name/spec again or the same semantic tokens would be counted twice.
        combined = raw_text or "".join(
            value for value in (normalized_name, normalized_spec) if value
        )
        if not combined:
            return CanonicalSourceIdentity(
                brand="",
                product="",
                spec="",
                schema_version=self.schema_version,
                issues=("source_phrase_missing",),
            )

        working = combined
        issues: list[str] = []

        brand = normalized_brand
        brand_matches = self._matches(combined, self.brands)
        if brand_matches:
            longest = len(brand_matches[0][1])
            strongest = {
                value.canonical
                for value, alias in brand_matches
                if len(alias) == longest
            }
            if len(strongest) > 1:
                issues.append("brand_ambiguous")
            elif not brand:
                brand = next(iter(strongest))
            for _, alias in brand_matches:
                working = self._remove_alias(working, alias)
        if brand:
            working = self._remove_alias(working, brand)

        subject_matches = self._matches(working, self.subjects)
        product = ""
        if subject_matches:
            longest = len(subject_matches[0][1])
            strongest = {
                value.canonical
                for value, alias in subject_matches
                if len(alias) == longest
            }
            if len(strongest) > 1:
                issues.append("product_subject_ambiguous")
            else:
                product = next(iter(strongest))
            for _, alias in subject_matches:
                working = self._remove_alias(working, alias)

        spec_values: list[str] = []
        for _, values in self.spec_dimensions:
            matches = self._matches(working, values)
            if not matches:
                continue
            longest = len(matches[0][1])
            strongest = {
                value.canonical
                for value, alias in matches
                if len(alias) == longest
            }
            if len(strongest) > 1:
                issues.append("specification_ambiguous")
                continue
            canonical = next(iter(strongest))
            spec_values.append(canonical)
            for _, alias in matches:
                working = self._remove_alias(working, alias)

        for pattern in self.quantity_unit_patterns:
            working = pattern.sub("", working)
        if normalized_unit:
            working = self._remove_alias(working, normalized_unit)

        if not product:
            fallback = normalized_name
            if brand:
                fallback = self._remove_alias(fallback, brand)
            for value, alias in brand_matches:
                fallback = self._remove_alias(fallback, alias)
            for _, values in self.spec_dimensions:
                for _, alias in self._matches(fallback, values):
                    fallback = self._remove_alias(fallback, alias)
            for pattern in self.quantity_unit_patterns:
                fallback = pattern.sub("", fallback)
            if normalized_unit:
                fallback = self._remove_alias(fallback, normalized_unit)
            product = fallback
            if product:
                working = self._remove_alias(working, product)

        residual = working
        if residual:
            spec_values.append(residual)
        spec = "|".join(dict.fromkeys(value for value in spec_values if value))
        if not product:
            issues.append("canonical_product_missing")
        return CanonicalSourceIdentity(
            brand=brand,
            product=product,
            spec=spec,
            schema_version=self.schema_version,
            residual=residual,
            issues=tuple(dict.fromkeys(issues)),
        )
