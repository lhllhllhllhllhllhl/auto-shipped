from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Callable, Iterable


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = SequenceMatcher(None, left, right).ratio()
    if left in right or right in left:
        ratio = max(ratio, 0.82)
    return ratio


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

    normalized_name = normalizer(source_name)
    normalized_spec = normalizer(source_spec)
    if not normalized_name:
        return ()

    ranked: dict[tuple[str, str], tuple[float, Any]] = {}
    for record in records:
        product_name = normalizer(getattr(record, "product_name", ""))
        if not product_name:
            continue
        name_score = _similarity(normalized_name, product_name)
        if name_score < 0.45:
            continue

        if normalized_spec:
            spec_score = max(
                _similarity(normalized_spec, normalizer(getattr(record, "spec_name", ""))),
                _similarity(normalized_spec, normalizer(getattr(record, "spec_code", ""))),
            )
            score = name_score * 0.78 + spec_score * 0.22
        else:
            score = name_score
        if score < minimum_score:
            continue

        key = (
            str(getattr(record, "product_code", "") or ""),
            str(getattr(record, "spec_code", "") or ""),
        )
        previous = ranked.get(key)
        if previous is None or score > previous[0]:
            ranked[key] = (score, record)

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            -item[0],
            str(getattr(item[1], "product_code", "") or ""),
            str(getattr(item[1], "spec_code", "") or ""),
        ),
    )
    return tuple(record for _, record in ordered[: max(1, limit)])
