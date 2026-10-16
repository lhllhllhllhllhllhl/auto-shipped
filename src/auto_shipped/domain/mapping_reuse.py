from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MappingReuseCandidate:
    """A current-batch product mapping that may be promoted after delivery.

    The candidate is informational.  It never persists itself and never grants
    upload authority; an Agent must still obtain an explicit future-reuse choice
    before invoking the governed Feishu publisher.
    """

    source_profile_id: str
    company_id: str
    identifier_type: str
    source_value: str
    source_spec: str
    source_fingerprint: str
    product_code: str
    spec_code: str
    product_name: str
    spec_name: str
    future_scope_options: tuple[str, ...] = ("source",)
    reason: str = "本批映射已确认并用于生成导入文件，但尚未进入正式映射库。"
    candidate_id: str = ""
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.candidate_id:
            return
        payload = {
            "source_profile_id": self.source_profile_id,
            "company_id": self.company_id,
            "identifier_type": self.identifier_type,
            "source_value": self.source_value,
            "source_spec": self.source_spec,
            "product_code": self.product_code,
            "spec_code": self.spec_code,
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        object.__setattr__(self, "candidate_id", f"M-{digest}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MappingReuseBundle:
    items: list[MappingReuseCandidate] = field(default_factory=list)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        numbered = [
            {"number": index, **item.to_dict()}
            for index, item in enumerate(self.items, start=1)
        ]
        return {
            "schema_version": self.schema_version,
            "status": "followup_available" if numbered else "not_required",
            "items": numbered,
            "question": (
                "以上新增映射已按仅本次使用完成出表。请确认哪些继续仅本次，"
                "哪些以后沿用；名称别名还可选择仅当前来源或所有来源沿用。"
                if numbered
                else ""
            ),
            "reply_examples": (
                [
                    "全部仅本次使用",
                    "第1项以后仅当前来源沿用，其余仅本次",
                    "第2项以后所有来源沿用，其余仅本次",
                ]
                if numbered
                else []
            ),
            "safety": {
                "non_blocking_after_delivery": True,
                "bundle_authorizes_persistence": False,
                "bundle_authorizes_upload": False,
                "persistence_requires_explicit_future_choice": True,
            },
        }


def deduplicate_mapping_reuse_candidates(
    items: list[MappingReuseCandidate],
) -> list[MappingReuseCandidate]:
    seen: set[str] = set()
    unique: list[MappingReuseCandidate] = []
    for item in items:
        if item.candidate_id in seen:
            continue
        seen.add(item.candidate_id)
        unique.append(item)
    return unique
