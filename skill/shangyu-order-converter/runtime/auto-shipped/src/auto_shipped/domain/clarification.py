from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


ClarificationScope = Literal["batch", "file", "order", "field"]
AnswerType = Literal["confirmation", "single_choice", "text", "file"]


@dataclass(frozen=True, slots=True)
class ClarificationRequest:
    code: str
    question: str
    reason: str
    scope: ClarificationScope = "file"
    source_ref: str | None = None
    field: str | None = None
    answer_type: AnswerType = "text"
    choices: tuple[str, ...] = ()
    next_action: str | None = None
    blocking: bool = True
    schema_version: str = "1.0"

    def to_dict(self) -> dict:
        return asdict(self)


def deduplicate_clarifications(
    requests: list[ClarificationRequest],
) -> list[ClarificationRequest]:
    seen: set[tuple[str, str | None, str | None]] = set()
    unique: list[ClarificationRequest] = []
    for request in requests:
        key = (request.code, request.source_ref, request.field)
        if key not in seen:
            seen.add(key)
            unique.append(request)
    return unique

