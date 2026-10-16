from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


DecisionReuseScope = Literal["current_batch", "future"]


@dataclass(frozen=True, slots=True)
class DecisionItem:
    """A non-blocking, privacy-safe decision attached to a preview artifact.

    Decision items never authorize upload or persistence.  They only record the
    exact provisional value used to produce a preview and the reuse choices an
    Agent may offer after the preview exists.
    """

    code: str
    source_ref: str
    field: str
    proposed_value: dict[str, Any]
    reason: str
    confirmation_question: str
    persistence_options: tuple[DecisionReuseScope, ...] = (
        "current_batch",
        "future",
    )
    next_action: str | None = None
    decision_id: str = ""
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.decision_id:
            return
        payload = {
            "code": self.code,
            "source_ref": self.source_ref,
            "field": self.field,
            "proposed_value": self.proposed_value,
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        object.__setattr__(self, "decision_id", f"D-{digest}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DecisionBundle:
    items: list[DecisionItem] = field(default_factory=list)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        numbered = []
        for index, item in enumerate(self.items, start=1):
            numbered.append({"number": index, **item.to_dict()})
        return {
            "schema_version": self.schema_version,
            "status": "awaiting_user_confirmation" if numbered else "not_required",
            "items": numbered,
            "reply_examples": [
                "全部仅本次使用",
                "全部确认，第1项以后沿用，其余仅本次",
                "第2项不对，改为……，其余仅本次",
            ] if numbered else [],
            "safety": {
                "preview_only": bool(numbered),
                "decision_bundle_authorizes_upload": False,
                "persistence_requires_explicit_future_choice": True,
            },
        }


def deduplicate_decisions(items: list[DecisionItem]) -> list[DecisionItem]:
    seen: set[str] = set()
    unique: list[DecisionItem] = []
    for item in items:
        if item.decision_id in seen:
            continue
        seen.add(item.decision_id)
        unique.append(item)
    return unique
