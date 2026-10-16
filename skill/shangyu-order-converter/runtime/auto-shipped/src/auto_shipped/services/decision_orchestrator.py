from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from auto_shipped.domain import (
    ClarificationRequest,
    DecisionItem,
    deduplicate_clarifications,
    deduplicate_decisions,
)


DecisionStatus = Literal["ready", "preview_ready", "needs_input"]


@dataclass(frozen=True, slots=True)
class DecisionState:
    status: DecisionStatus
    clarifications: tuple[ClarificationRequest, ...]
    decisions: tuple[DecisionItem, ...]

    @property
    def may_create_upload_manifest(self) -> bool:
        return self.status == "ready"


def converge_decisions(
    *,
    clarifications: list[ClarificationRequest],
    decisions: list[DecisionItem],
) -> DecisionState:
    """Select exactly one conversion outcome without weakening blockers."""

    blocking = tuple(deduplicate_clarifications(clarifications))
    provisional = tuple(deduplicate_decisions(decisions))
    if blocking:
        # A blocker means no preview artifact exists.  Do not leak provisional
        # decisions (including masked recipient review) into the blocking
        # response, otherwise Agents tend to mix pre-file and post-file steps.
        return DecisionState("needs_input", blocking, ())
    if provisional:
        return DecisionState("preview_ready", (), provisional)
    return DecisionState("ready", (), ())
