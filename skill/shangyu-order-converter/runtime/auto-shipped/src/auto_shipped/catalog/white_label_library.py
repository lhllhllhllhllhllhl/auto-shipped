from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


class WhiteLabelLibraryError(ValueError):
    """Raised when the white-label SKU library cannot be used safely."""


class ProductIdentity(Protocol):
    product_code: str
    spec_code: str


@dataclass(frozen=True, slots=True)
class WhiteLabelLibrary:
    library_id: str
    version: int
    remark_token: str
    sku_keys: frozenset[tuple[str, str]]

    def matches_any(self, products: Iterable[ProductIdentity]) -> bool:
        return any(
            (str(product.product_code).strip(), str(product.spec_code).strip())
            in self.sku_keys
            for product in products
        )


def load_white_label_library(path: str | Path) -> WhiteLabelLibrary:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WhiteLabelLibraryError(f"无法读取白标SKU库: {exc}") from exc

    if payload.get("schema_version") != "1.0":
        raise WhiteLabelLibraryError("白标SKU库schema_version必须为1.0")
    if payload.get("status") != "active":
        raise WhiteLabelLibraryError("白标SKU库未处于active状态")

    match_policy = payload.get("match_policy") or {}
    if match_policy.get("mode") != "exact_product_and_spec_code":
        raise WhiteLabelLibraryError("白标SKU库只允许商品代码+规格代码精确匹配")
    if match_policy.get("name_similarity_allowed") is not False:
        raise WhiteLabelLibraryError("白标SKU库必须明确禁止名称相似匹配")

    order_policy = payload.get("order_policy") or {}
    if order_policy.get("if_any_item_matches") != "mark_entire_order":
        raise WhiteLabelLibraryError("白标SKU库必须使用任一商品命中则整单标记策略")
    if order_policy.get("target_field") != "卖家备注":
        raise WhiteLabelLibraryError("白标提示只能写入卖家备注")
    remark_token = str(order_policy.get("remark_token") or "").strip()
    if not remark_token:
        raise WhiteLabelLibraryError("白标SKU库缺少remark_token")

    sku_keys: set[tuple[str, str]] = set()
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise WhiteLabelLibraryError("白标SKU库entries必须为数组")
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise WhiteLabelLibraryError(f"白标SKU库第{index}项不是对象")
        status = str(entry.get("status") or "").strip()
        if status != "confirmed":
            raise WhiteLabelLibraryError(
                f"白标SKU库第{index}项状态必须为confirmed"
            )
        raw_product_code = str(entry.get("product_code") or "")
        raw_spec_code = str(entry.get("spec_code") or "")
        key = (raw_product_code.strip(), raw_spec_code.strip())
        if not all(key):
            raise WhiteLabelLibraryError(f"白标SKU库第{index}项缺少商品代码或规格代码")
        if key != (raw_product_code, raw_spec_code):
            raise WhiteLabelLibraryError(
                f"白标SKU库第{index}项代码包含未清理的首尾空白字符"
            )
        if key in sku_keys:
            raise WhiteLabelLibraryError(
                f"白标SKU库存在重复商品规格: {key[0]} / {key[1]}"
            )
        sku_keys.add(key)

    source_snapshot = payload.get("source_snapshot") or {}
    expected_count = source_snapshot.get("source_row_count")
    if expected_count is not None and expected_count != len(sku_keys):
        raise WhiteLabelLibraryError(
            "白标SKU库条目数量与source_snapshot.source_row_count不一致"
        )

    library_id = str(payload.get("library_id") or "").strip()
    version = payload.get("version")
    if not library_id or not isinstance(version, int) or version < 1:
        raise WhiteLabelLibraryError("白标SKU库缺少有效library_id或version")
    return WhiteLabelLibrary(
        library_id=library_id,
        version=version,
        remark_token=remark_token,
        sku_keys=frozenset(sku_keys),
    )
