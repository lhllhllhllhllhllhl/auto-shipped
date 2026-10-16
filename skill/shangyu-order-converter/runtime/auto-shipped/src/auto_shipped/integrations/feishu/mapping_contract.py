from __future__ import annotations

from typing import Literal


GLOBAL_MAPPING_SCOPE_ID = "*"
MappingScope = Literal["source", "global"]


class MappingScopeError(ValueError):
    """Raised when an official mapping mixes global and source-only identity."""


def normalize_mapping_scope(
    *,
    company_id: str,
    source_profile_id: str,
    identifier_type: str,
    source_value: str = "",
) -> MappingScope:
    """Validate and derive one mapping's scope from the existing sheet columns.

    The Feishu sheet keeps its stable fifteen-column layout.  A global product
    alias is represented by ``company_id=*`` and ``source_profile_id=*``.  This
    is deliberately restricted to product-name aliases; company product codes
    and barcodes are not assumed to be globally unique.
    """

    company = str(company_id or "").strip()
    profile = str(source_profile_id or "").strip()
    kind = str(identifier_type or "").strip()
    company_is_global = company == GLOBAL_MAPPING_SCOPE_ID
    profile_is_global = profile == GLOBAL_MAPPING_SCOPE_ID
    if company_is_global != profile_is_global:
        raise MappingScopeError(
            "全局映射必须同时使用company_id=*和source_profile_id=*；禁止混合全局与来源作用域。"
        )
    if company_is_global:
        if kind != "product_name":
            raise MappingScopeError(
                "全局映射只允许明确的商品名称别名；来源商品代码和条码必须按来源隔离。"
            )
        if not str(source_value or "").strip():
            raise MappingScopeError("全局商品名称别名必须填写source_value。")
        return "global"
    if not company or not profile:
        raise MappingScopeError("来源专用映射必须同时包含company_id和source_profile_id。")
    return "source"


def scoped_mapping_identity(
    *,
    mapping_scope: str,
    company_id: str,
    source_profile_id: str,
    identifier_type: str,
    source_value: str = "",
) -> tuple[str, str, MappingScope]:
    """Return canonical company/profile values for a requested mapping scope."""

    requested = str(mapping_scope or "source").strip()
    if requested == "global":
        company = GLOBAL_MAPPING_SCOPE_ID
        profile = GLOBAL_MAPPING_SCOPE_ID
    elif requested == "source":
        company = str(company_id or "").strip()
        profile = str(source_profile_id or "").strip()
    else:
        raise MappingScopeError(f"未知mapping_scope：{requested}")
    resolved = normalize_mapping_scope(
        company_id=company,
        source_profile_id=profile,
        identifier_type=identifier_type,
        source_value=source_value,
    )
    return company, profile, resolved
