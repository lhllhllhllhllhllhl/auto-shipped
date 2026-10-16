from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Callable, Iterable


@dataclass(frozen=True, slots=True)
class RankedProductCandidate:
    """One read-only catalog candidate together with its matching evidence.

    A candidate being the only surviving fuzzy result does not make it safe.
    Callers must use ``confidence_tier`` instead of candidate count when
    deciding whether a preview may be rendered.
    """

    product: Any
    rank: int
    name_score: float
    spec_score: float
    combined_score: float
    score_margin: float
    matched_attributes: tuple[str, ...] = ()
    missing_attributes: tuple[str, ...] = ()
    conflicting_attributes: tuple[str, ...] = ()
    confidence_tier: str = "blocking"
    confidence_reason: str = ""

    def public_evidence(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "name_score": round(self.name_score, 4),
            "spec_score": round(self.spec_score, 4),
            "combined_score": round(self.combined_score, 4),
            "score_margin": round(self.score_margin, 4),
            "matched_attributes": list(self.matched_attributes),
            "missing_attributes": list(self.missing_attributes),
            "conflicting_attributes": list(self.conflicting_attributes),
            "confidence_tier": self.confidence_tier,
            "confidence_reason": self.confidence_reason,
        }


@dataclass(frozen=True, slots=True)
class CandidateConfidencePolicy:
    """Shared fuzzy-candidate gate used by every company and text source."""

    minimum_score: float = 0.58
    preview_minimum_score: float = 0.84
    preview_minimum_name_score: float = 0.80
    preview_minimum_spec_score: float = 0.78
    preview_minimum_margin: float = 0.08


DEFAULT_CANDIDATE_CONFIDENCE_POLICY = CandidateConfidencePolicy()


# These are semantic categories, not product mappings.  They help detect that
# a fuzzy result dropped or contradicted an attribute explicitly supplied by
# the source.  Exact aliases and confirmed mappings remain the authority.
_ATTRIBUTE_ALIASES: dict[str, tuple[tuple[str, str], ...]] = {
    "颜色": (
        ("deep blue", "深蓝"),
        ("navy", "藏蓝"),
        ("denim blue", "牛仔蓝"),
        ("深蓝色", "深蓝"),
        ("深蓝", "深蓝"),
        ("藏蓝色", "藏蓝"),
        ("藏蓝", "藏蓝"),
        ("宝石蓝", "宝石蓝"),
        ("牛仔蓝", "牛仔蓝"),
        ("浅蓝色", "浅蓝"),
        ("浅蓝", "浅蓝"),
        ("蓝色", "蓝"),
        ("蓝", "蓝"),
        ("粉红色", "粉"),
        ("粉色", "粉"),
        ("珍珠粉", "珍珠粉"),
        ("橘色", "橘"),
        ("橙色", "橘"),
        ("绿色", "绿"),
        ("珍珠绿", "珍珠绿"),
        ("黄色", "黄"),
        ("金色", "金"),
        ("白色", "白"),
        ("黑色", "黑"),
        ("紫色", "紫"),
        ("红色", "红"),
        ("orange", "橘"),
        ("green", "绿"),
        ("yellow", "黄"),
        ("white", "白"),
        ("black", "黑"),
        ("pink", "粉"),
        ("blue", "蓝"),
        ("gold", "金"),
    ),
    "软硬": (
        ("超超软", "超超软"),
        ("超软", "超软"),
        ("超硬", "超硬"),
        ("加硬", "加硬"),
        ("硬款", "硬"),
        ("硬型", "硬"),
        ("柔软", "软"),
        ("软款", "软"),
        ("普通", "普通"),
        ("标准", "普通"),
    ),
    "品类": (
        ("搓澡巾", "搓澡巾"),
        ("搓背巾", "搓澡巾"),
        ("沐浴巾", "搓澡巾"),
        ("百洁布", "百洁布"),
        ("海绵", "海绵"),
        ("手套", "手套"),
        ("玉米", "玉米"),
        ("毛巾", "毛巾"),
    ),
}

_NON_BRAND_LATIN_TOKENS = {
    "black",
    "blue",
    "deep",
    "denim",
    "gold",
    "green",
    "navy",
    "orange",
    "pink",
    "white",
    "yellow",
}


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = SequenceMatcher(None, left, right).ratio()
    shorter = left if len(left) <= len(right) else right
    meaningful_substring = len(shorter) >= 3 or (
        len(shorter) >= 2 and any(ord(character) > 127 for character in shorter)
    )
    if meaningful_substring and (left in right or right in left):
        ratio = max(ratio, 0.82)
    return ratio


def _normalized_for_attributes(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        character
        for character in text
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _extract_attributes(*values: Any) -> dict[str, str]:
    raw_text = " ".join(str(value or "") for value in values)
    normalized = _normalized_for_attributes(raw_text)
    attributes: dict[str, str] = {}
    for category, aliases in _ATTRIBUTE_ALIASES.items():
        for alias, canonical in aliases:
            if _normalized_for_attributes(alias) in normalized:
                attributes[category] = canonical
                break

    latin_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z]{2,}", raw_text)
        if token.casefold() not in _NON_BRAND_LATIN_TOKENS
    }
    if latin_tokens:
        attributes["拉丁标识"] = "/".join(sorted(latin_tokens))
    return attributes


def _compare_attributes(
    source_attributes: dict[str, str],
    target_attributes: dict[str, str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    matched: list[str] = []
    missing: list[str] = []
    conflicting: list[str] = []
    for category, source_value in source_attributes.items():
        target_value = target_attributes.get(category)
        if not target_value:
            missing.append(f"{category}:{source_value}")
        elif target_value == source_value:
            matched.append(f"{category}:{source_value}")
        elif _compatible_attribute_values(category, source_value, target_value):
            matched.append(f"{category}:{source_value}~{target_value}")
        else:
            conflicting.append(f"{category}:{source_value}!={target_value}")
    return tuple(matched), tuple(missing), tuple(conflicting)


_COLOR_FAMILIES = {
    "蓝": "蓝",
    "深蓝": "蓝",
    "藏蓝": "蓝",
    "宝石蓝": "蓝",
    "牛仔蓝": "蓝",
    "浅蓝": "蓝",
}


def _compatible_attribute_values(
    category: str,
    source_value: str,
    target_value: str,
) -> bool:
    """Return conservative semantic compatibility without collapsing SKU identity.

    Blue shades remain distinct canonical values.  They are only treated as
    compatible evidence when another gate (for example a confirmed alias
    family plus hardness) has already narrowed the candidate set.  Multiple
    compatible blue variants therefore remain ambiguous instead of being
    silently merged.
    """

    if category != "颜色":
        return False
    source_family = _COLOR_FAMILIES.get(source_value)
    target_family = _COLOR_FAMILIES.get(target_value)
    return bool(source_family and source_family == target_family)


def rank_alias_family_candidates(
    records: Iterable[Any],
    *,
    source_spec: str,
    normalizer: Callable[[Any], str],
    limit: int = 5,
    policy: CandidateConfidencePolicy = DEFAULT_CANDIDATE_CONFIDENCE_POLICY,
) -> tuple[RankedProductCandidate, ...]:
    """Rank variants inside a family established by confirmed name aliases.

    The caller is responsible for proving that ``records`` belong to one
    stable target-code family learned from at least two official mappings for
    the same source product name.  This function only compares the still-new
    source specification (colour, hardness, and similar explicit attributes)
    with those family variants.  It never confirms or persists a mapping.
    """

    normalized_spec = normalizer(source_spec)
    source_attributes = _extract_attributes(source_spec)
    if not normalized_spec or not source_attributes:
        return ()

    ranked: dict[tuple[str, str], tuple[float, RankedProductCandidate]] = {}
    for record in records:
        target_attributes = _extract_attributes(
            getattr(record, "product_name", ""),
            getattr(record, "spec_name", ""),
        )
        matched, missing, conflicting = _compare_attributes(
            source_attributes,
            target_attributes,
        )
        # A family hint narrows the product category, but every attribute that
        # the source explicitly supplied as its spec must still be represented
        # by the target.  Missing/conflicting variants are not safe candidates.
        if missing or conflicting:
            continue

        lexical_score = max(
            _similarity(
                normalized_spec,
                normalizer(getattr(record, "product_name", "")),
            ),
            _similarity(
                normalized_spec,
                normalizer(getattr(record, "spec_name", "")),
            ),
        )
        exact_count = sum("~" not in item for item in matched)
        compatible_count = len(matched) - exact_count
        semantic_score = (
            exact_count + compatible_count * 0.85
        ) / max(1, len(source_attributes))
        combined_score = semantic_score * 0.85 + lexical_score * 0.15
        evidence = RankedProductCandidate(
            product=record,
            rank=0,
            # The product-name identity was established by the caller's
            # official alias family, rather than by fuzzy catalog text.
            name_score=1.0,
            spec_score=semantic_score,
            combined_score=combined_score,
            score_margin=0.0,
            matched_attributes=matched,
            missing_attributes=(),
            conflicting_attributes=(),
        )
        key = (
            str(getattr(record, "product_code", "") or ""),
            str(getattr(record, "spec_code", "") or ""),
        )
        previous = ranked.get(key)
        if previous is None or combined_score > previous[0]:
            ranked[key] = (combined_score, evidence)

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            -item[0],
            str(getattr(item[1].product, "product_code", "") or ""),
            str(getattr(item[1].product, "spec_code", "") or ""),
        ),
    )[: max(1, limit)]
    results: list[RankedProductCandidate] = []
    for index, (score, evidence) in enumerate(ordered):
        next_score = ordered[index + 1][0] if index + 1 < len(ordered) else 0.0
        margin = score - next_score
        preview_safe = (
            index == 0
            and len(ordered) == 1
            and evidence.spec_score >= policy.preview_minimum_spec_score
            and margin >= policy.preview_minimum_margin
        )
        results.append(
            replace(
                evidence,
                rank=index + 1,
                score_margin=margin,
                confidence_tier="preview_safe" if preview_safe else "blocking",
                confidence_reason=(
                    "同一来源名称已有多个正式映射形成稳定商品系列，且新规格在系列内唯一完整命中"
                    if preview_safe
                    else "正式映射商品系列内仍有多个规格候选，必须由用户选择"
                ),
            )
        )
    return tuple(results)


def rank_product_candidates(
    records: Iterable[Any],
    *,
    source_name: str,
    source_spec: str,
    normalizer: Callable[[Any], str],
    limit: int = 5,
    policy: CandidateConfidencePolicy = DEFAULT_CANDIDATE_CONFIDENCE_POLICY,
) -> tuple[RankedProductCandidate, ...]:
    """Rank candidates and retain enough evidence for a safe downstream gate."""

    normalized_name = normalizer(source_name)
    normalized_spec = normalizer(source_spec)
    if not normalized_name:
        return ()

    source_attributes = _extract_attributes(source_name, source_spec)
    ranked: dict[tuple[str, str], tuple[float, RankedProductCandidate]] = {}
    for record in records:
        product_name = normalizer(getattr(record, "product_name", ""))
        if not product_name:
            continue
        name_score = _similarity(normalized_name, product_name)
        if name_score < 0.45:
            continue

        spec_score = 0.0
        if normalized_spec:
            spec_score = max(
                _similarity(
                    normalized_spec,
                    normalizer(getattr(record, "spec_name", "")),
                ),
                _similarity(
                    normalized_spec,
                    normalizer(getattr(record, "spec_code", "")),
                ),
            )
            score = name_score * 0.78 + spec_score * 0.22
        else:
            score = name_score
        if score < policy.minimum_score:
            continue

        target_attributes = _extract_attributes(
            getattr(record, "product_name", ""),
            getattr(record, "spec_name", ""),
        )
        matched, missing, conflicting = _compare_attributes(
            source_attributes,
            target_attributes,
        )
        evidence = RankedProductCandidate(
            product=record,
            rank=0,
            name_score=name_score,
            spec_score=spec_score,
            combined_score=score,
            score_margin=0.0,
            matched_attributes=matched,
            missing_attributes=missing,
            conflicting_attributes=conflicting,
        )
        key = (
            str(getattr(record, "product_code", "") or ""),
            str(getattr(record, "spec_code", "") or ""),
        )
        previous = ranked.get(key)
        if previous is None or score > previous[0]:
            ranked[key] = (score, evidence)

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            -item[0],
            str(getattr(item[1].product, "product_code", "") or ""),
            str(getattr(item[1].product, "spec_code", "") or ""),
        ),
    )[: max(1, limit)]
    results: list[RankedProductCandidate] = []
    for index, (score, evidence) in enumerate(ordered):
        next_score = ordered[index + 1][0] if index + 1 < len(ordered) else 0.0
        margin = score - next_score
        preview_safe = (
            index == 0
            and len(ordered) == 1
            and score >= policy.preview_minimum_score
            and evidence.name_score >= policy.preview_minimum_name_score
            and (
                not normalized_spec
                or evidence.spec_score >= policy.preview_minimum_spec_score
            )
            and margin >= policy.preview_minimum_margin
            and not evidence.missing_attributes
            and not evidence.conflicting_attributes
        )
        if preview_safe:
            confidence_reason = "唯一候选的名称、规格和关键语义属性均达到预览门槛"
        elif evidence.conflicting_attributes:
            confidence_reason = "候选与来源的关键语义属性冲突"
        elif evidence.missing_attributes:
            confidence_reason = "候选缺少来源明确给出的关键语义属性"
        elif len(ordered) > 1:
            confidence_reason = "模糊匹配存在多个候选"
        else:
            confidence_reason = "候选相似度或候选间距不足以安全生成预览"
        results.append(
            replace(
                evidence,
                rank=index + 1,
                score_margin=margin,
                confidence_tier="preview_safe" if preview_safe else "blocking",
                confidence_reason=confidence_reason,
            )
        )
    return tuple(results)


def rank_similar_products(
    records: Iterable[Any],
    *,
    source_name: str,
    source_spec: str,
    normalizer: Callable[[Any], str],
    limit: int = 5,
    minimum_score: float = 0.58,
) -> tuple[Any, ...]:
    """Return deterministic, read-only candidates; never assert a mapping.

    The caller remains responsible for user confirmation and persistence.  The
    matcher only ranks product master records and deliberately has no access to
    order output or mapping storage.
    """

    policy = replace(
        DEFAULT_CANDIDATE_CONFIDENCE_POLICY,
        minimum_score=minimum_score,
    )
    return tuple(
        candidate.product
        for candidate in rank_product_candidates(
            records,
            source_name=source_name,
            source_spec=source_spec,
            normalizer=normalizer,
            limit=limit,
            policy=policy,
        )
    )
