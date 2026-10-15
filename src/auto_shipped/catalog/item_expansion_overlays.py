from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .product_catalog import ProductCatalog, clean_identifier


CURRENT_BATCH_ITEM_EXPANSION_CONFIRMATION = (
    "CONFIRM_USE_ITEM_EXPANSION_FOR_CURRENT_BATCH"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class BatchItemExpansionError(ValueError):
    """Raised when a current-batch one-to-many rule is not safe to save."""


def save_batch_item_expansion(
    output_path: str | Path,
    *,
    catalog: ProductCatalog,
    company_id: Any,
    source_profile_id: Any,
    identifier_type: Any,
    source_value: Any,
    source_spec: Any,
    target_platform: Any,
    source_fingerprint: Any,
    components: Sequence[Mapping[str, Any]],
    evidence: Any,
    confirmation_token: str,
) -> Path:
    """Write one file-fingerprint-bound expansion overlay after explicit approval."""

    if confirmation_token != CURRENT_BATCH_ITEM_EXPANSION_CONFIRMATION:
        raise BatchItemExpansionError("缺少当前批次组合商品确认口令。")
    company = clean_identifier(company_id)
    profile = clean_identifier(source_profile_id)
    kind = clean_identifier(identifier_type)
    source = clean_identifier(source_value)
    spec = clean_identifier(source_spec)
    platform = clean_identifier(target_platform)
    fingerprint = clean_identifier(source_fingerprint).lower()
    reason = clean_identifier(evidence)
    if not company or not profile:
        raise BatchItemExpansionError("当前批次组合规则缺少公司或来源配置。")
    if kind not in {"product_code", "barcode", "product_name"}:
        raise BatchItemExpansionError("当前批次组合规则的标识类型无效。")
    if not source and not spec:
        raise BatchItemExpansionError("来源值和来源规格不能同时为空。")
    if platform != "guanyi":
        raise BatchItemExpansionError("当前批次组合规则只支持管易目标。")
    if not _SHA256.fullmatch(fingerprint):
        raise BatchItemExpansionError("当前批次组合规则必须绑定sha256来源指纹。")
    if not reason:
        raise BatchItemExpansionError("当前批次组合规则必须保留用户确认依据。")
    if len(components) < 2:
        raise BatchItemExpansionError("组合规则至少需要两个目标商品。")

    normalized_components: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, component in enumerate(components, start=1):
        target = catalog.resolve_unique_target(
            component.get("product_code"), component.get("spec_code")
        )
        try:
            multiplier = float(component.get("quantity_multiplier"))
        except (TypeError, ValueError):
            multiplier = 0
        if target is None or multiplier <= 0:
            raise BatchItemExpansionError(
                f"第{index}个目标商品不存在、不唯一或数量倍数无效。"
            )
        pair = (target.product_code, target.spec_code)
        if pair in seen_targets:
            raise BatchItemExpansionError("组合规则包含重复目标，请合并数量倍数。")
        seen_targets.add(pair)
        normalized_components.append(
            {
                "component_index": index,
                "product_code": target.product_code,
                "spec_code": target.spec_code,
                "quantity_multiplier": multiplier,
            }
        )

    identity = "\x1f".join((company, profile, kind, source, spec, platform, fingerprint))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    document = {
        "schema_version": "1.0",
        "source": "current_batch_item_expansion_overlay",
        "item_expansions": [
            {
                "rule_id": f"current-batch-item-expansion-{digest[:24]}",
                "rule_key": f"current-batch-item-expansion-{digest[:24]}",
                "company_id": company,
                "source_profile_id": profile,
                "identifier_type": kind,
                "source_value": source,
                "source_spec": spec,
                "target_platform": platform,
                "confirmed": True,
                "reuse_scope": "current_batch",
                "source_fingerprint": fingerprint,
                "mapping_authority": "current_batch_user_confirmation",
                "evidence": reason,
                "components": normalized_components,
            }
        ],
    }
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination
