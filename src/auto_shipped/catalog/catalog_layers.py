from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable


def _clean(value: Any) -> str:
    return str(value or "").replace("\t", "").strip()


def _number(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class CatalogLayer:
    source_id: str
    path: Path
    priority: int
    require_product_code: bool = True


@dataclass(frozen=True, slots=True)
class LayeredProductRow:
    product_code: str
    product_name: str
    spec_code: str
    spec_name: str
    weight_grams: float | None
    default_warehouse: str
    catalog_source_id: str
    catalog_priority: int
    catalog_source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogLayerStats:
    source_id: str
    priority: int
    active_rows: int
    pending_rows: int


@dataclass(frozen=True, slots=True)
class EffectiveCatalogBuild:
    rows: tuple[LayeredProductRow, ...]
    layers: tuple[CatalogLayerStats, ...]


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别商品文件编码: {path.name}")


def _read_layer(layer: CatalogLayer) -> tuple[list[LayeredProductRow], CatalogLayerStats]:
    rows = csv.DictReader(_read_text(layer.path).splitlines())
    if rows.fieldnames is None:
        raise ValueError(f"商品目录没有表头: {layer.path.name}")
    required = {"商品代码", "商品名称", "规格代码", "规格名称"}
    missing = required.difference(_clean(value) for value in rows.fieldnames)
    if missing:
        raise ValueError(
            f"商品目录缺少必要列 {sorted(missing)}: {layer.path.name}"
        )

    active: list[LayeredProductRow] = []
    pending = 0
    for row_number, row in enumerate(rows, start=2):
        status = _clean(row.get("目录状态") or "active").casefold()
        if status not in {"active", "enabled", "生效"}:
            if any(_clean(row.get(key)) for key in required):
                pending += 1
            continue
        product_code = _clean(row.get("商品代码"))
        product_name = _clean(row.get("商品名称"))
        spec_code = _clean(row.get("规格代码"))
        spec_name = _clean(row.get("规格名称"))
        if not any((product_code, product_name, spec_code, spec_name)):
            continue
        if not product_code and layer.require_product_code:
            raise ValueError(
                f"生效商品缺少商品代码: {layer.path.name} 第{row_number}行"
            )
        active.append(
            LayeredProductRow(
                product_code=product_code,
                product_name=product_name,
                spec_code=spec_code,
                spec_name=spec_name,
                weight_grams=_number(row.get("重量")),
                default_warehouse=_clean(row.get("默认仓库")),
                catalog_source_id=layer.source_id,
                catalog_priority=layer.priority,
                catalog_source_ids=(layer.source_id,),
            )
        )
    return active, CatalogLayerStats(
        source_id=layer.source_id,
        priority=layer.priority,
        active_rows=len(active),
        pending_rows=pending,
    )


def _agreed_lower_value(values: Iterable[Any]) -> Any:
    present = [value for value in values if value not in (None, "")]
    if not present:
        return None
    first = present[0]
    return first if all(value == first for value in present[1:]) else None


def _enrich_highest_priority_rows(
    highest: list[LayeredProductRow],
    lower: list[LayeredProductRow],
) -> list[LayeredProductRow]:
    if len(highest) != 1 or not lower:
        return highest
    winner = highest[0]
    sources = tuple(
        dict.fromkeys(
            [
                winner.catalog_source_id,
                *(row.catalog_source_id for row in lower),
            ]
        )
    )
    return [
        replace(
            winner,
            product_name=winner.product_name
            or (_agreed_lower_value(row.product_name for row in lower) or ""),
            spec_name=winner.spec_name
            or (_agreed_lower_value(row.spec_name for row in lower) or ""),
            weight_grams=(
                winner.weight_grams
                if winner.weight_grams is not None
                else _agreed_lower_value(row.weight_grams for row in lower)
            ),
            default_warehouse=winner.default_warehouse
            or (_agreed_lower_value(row.default_warehouse for row in lower) or ""),
            catalog_source_ids=sources,
        )
    ]


def build_effective_catalog(layers: Iterable[CatalogLayer]) -> EffectiveCatalogBuild:
    loaded: list[LayeredProductRow] = []
    stats: list[CatalogLayerStats] = []
    for layer in sorted(layers, key=lambda item: (-item.priority, item.source_id)):
        if not layer.path.is_file():
            raise FileNotFoundError(layer.path)
        rows, layer_stats = _read_layer(layer)
        loaded.extend(rows)
        stats.append(layer_stats)

    by_pair: dict[tuple[str, str], list[LayeredProductRow]] = {}
    for row in loaded:
        by_pair.setdefault((row.product_code, row.spec_code), []).append(row)

    effective: list[LayeredProductRow] = []
    for key in sorted(by_pair):
        candidates = by_pair[key]
        highest_priority = max(row.catalog_priority for row in candidates)
        highest = [
            row for row in candidates if row.catalog_priority == highest_priority
        ]
        lower = [row for row in candidates if row.catalog_priority < highest_priority]
        effective.extend(_enrich_highest_priority_rows(highest, lower))

    return EffectiveCatalogBuild(rows=tuple(effective), layers=tuple(stats))
