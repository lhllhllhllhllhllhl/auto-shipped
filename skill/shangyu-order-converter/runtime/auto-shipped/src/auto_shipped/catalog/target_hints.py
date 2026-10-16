from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .product_candidates import rank_similar_products
from .product_catalog import (
    ProductCatalog,
    ProductRecord,
    clean_identifier,
    normalize_product_text,
)


TargetHintType = Literal[
    "auto",
    "spec_code",
    "product_code",
    "barcode",
    "product_name",
]


@dataclass(frozen=True, slots=True)
class TargetHintResolution:
    status: str
    raw_hint: str
    declared_type: str
    match_method: str
    reason: str
    product: ProductRecord | None = None
    candidates: tuple[ProductRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["product"] = asdict(self.product) if self.product else None
        payload["candidates"] = [asdict(item) for item in self.candidates]
        return payload


def _unique(records: list[ProductRecord]) -> tuple[ProductRecord, ...]:
    unique: dict[tuple[str, str], ProductRecord] = {}
    for record in records:
        unique[(record.product_code, record.spec_code)] = record
    return tuple(unique.values())


def _code_tokens(raw_hint: str) -> tuple[str, ...]:
    stripped = unicodedata.normalize("NFKC", raw_hint).strip().strip("`'\"“”‘’")
    tokens = [stripped]
    tokens.extend(
        token
        for token in re.split(r"[\s,，;/；]+", stripped)
        if token and token != stripped
    )
    tokens.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", stripped))
    seen: list[str] = []
    for token in tokens:
        cleaned = clean_identifier(token)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def _exact_candidates(
    catalog: ProductCatalog,
    raw_hint: str,
    declared_type: str,
) -> tuple[tuple[ProductRecord, ...], str]:
    tokens = _code_tokens(raw_hint)
    spec_matches: list[ProductRecord] = []
    product_matches: list[ProductRecord] = []
    product_name_matches: list[ProductRecord] = []
    spec_name_matches: list[ProductRecord] = []
    normalized_hint = normalize_product_text(raw_hint)
    normalized_name_segments = {
        normalize_product_text(value)
        for value in re.split(r"[,，;/；]+", raw_hint)
        if normalize_product_text(value)
    }
    normalized_name_segments.add(normalized_hint)
    for record in catalog.records:
        product_code = clean_identifier(record.product_code)
        spec_code = clean_identifier(record.spec_code)
        product_name = normalize_product_text(record.product_name)
        spec_name = normalize_product_text(record.spec_name)
        code_product_match = any(token == product_code for token in tokens)
        code_spec_match = any(token == spec_code for token in tokens if spec_code)
        if code_spec_match:
            spec_matches.append(record)
        if code_product_match:
            product_matches.append(record)
        if product_name and product_name in normalized_name_segments:
            product_name_matches.append(record)
        if spec_name and spec_name in normalized_name_segments:
            spec_name_matches.append(record)
    product_name_keys = {
        (record.product_code, record.spec_code) for record in product_name_matches
    }
    spec_name_keys = {
        (record.product_code, record.spec_code) for record in spec_name_matches
    }
    if product_name_keys and spec_name_keys:
        exact_name_keys = product_name_keys & spec_name_keys
        name_matches = [
            record
            for record in catalog.records
            if (record.product_code, record.spec_code) in exact_name_keys
        ]
    else:
        name_matches = [*product_name_matches, *spec_name_matches]
    declared_index = {
        "spec_code": spec_matches,
        "product_code": product_matches,
        "barcode": [*spec_matches, *product_matches],
        "product_name": name_matches,
    }
    declared = _unique(declared_index.get(declared_type, []))
    if declared_type != "auto" and declared:
        return declared, f"exact_{declared_type}"
    # Specification/barcode identity is more specific than a parent product
    # code.  If the user includes both in one reply, the spec narrows the SKU
    # rather than the broader product-code match making it look ambiguous.
    for values in (spec_matches, product_matches, name_matches):
        exact = _unique(values)
        if exact:
            method = (
                "exact_auto_identifier"
                if declared_type == "auto"
                else "exact_cross_identifier_fallback"
            )
            return exact, method
    return (), ""


def resolve_target_hint(
    catalog: ProductCatalog,
    raw_hint: Any,
    *,
    declared_type: TargetHintType = "auto",
) -> TargetHintResolution:
    """Resolve a user's target-side hint without touching source order identity.

    The caller supplies the conversational role (this is an answer to a target
    product question).  The resolver accepts codes, barcodes, names, or specs,
    searches the effective target catalog, and only returns ``resolved`` for an
    exact unique SKU pair.  Similarity can suggest candidates but never confirms
    them.
    """

    hint = clean_identifier(raw_hint)
    if declared_type not in {
        "auto",
        "spec_code",
        "product_code",
        "barcode",
        "product_name",
    }:
        raise ValueError(f"未知target hint类型：{declared_type}")
    if not hint:
        return TargetHintResolution(
            status="not_found",
            raw_hint="",
            declared_type=declared_type,
            match_method="empty_target_hint",
            reason="用户没有提供可解析的目标商品线索。",
        )

    exact, method = _exact_candidates(catalog, hint, declared_type)
    if len(exact) == 1:
        return TargetHintResolution(
            status="resolved",
            raw_hint=hint,
            declared_type=declared_type,
            match_method=method,
            reason="用户提供的目标线索在有效商品目录中精确且唯一命中。",
            product=exact[0],
            candidates=exact,
        )
    if exact:
        return TargetHintResolution(
            status="ambiguous",
            raw_hint=hint,
            declared_type=declared_type,
            match_method=method,
            reason="用户提供的目标线索精确命中多个规格，必须选择具体商品规格。",
            candidates=exact,
        )

    candidates = rank_similar_products(
        catalog.records,
        source_name=hint,
        source_spec="",
        normalizer=normalize_product_text,
    )
    if len(candidates) == 1:
        return TargetHintResolution(
            status="candidate",
            raw_hint=hint,
            declared_type=declared_type,
            match_method="fuzzy_target_name_candidate",
            reason="目标线索仅相似命中一个候选，仍需用户确认，不能直接保存映射。",
            product=candidates[0],
            candidates=candidates,
        )
    if candidates:
        return TargetHintResolution(
            status="ambiguous",
            raw_hint=hint,
            declared_type=declared_type,
            match_method="fuzzy_target_name_candidates",
            reason="目标线索相似命中多个候选，必须由用户选择。",
            candidates=candidates,
        )
    return TargetHintResolution(
        status="not_found",
        raw_hint=hint,
        declared_type=declared_type,
        match_method="target_hint_not_found",
        reason="用户提供的目标线索未在当前有效商品目录中找到。",
    )
