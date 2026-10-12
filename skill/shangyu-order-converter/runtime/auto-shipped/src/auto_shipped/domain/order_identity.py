from __future__ import annotations

from dataclasses import dataclass

from .parsed_order import ParsedSource


class OrderIdentityError(ValueError):
    """Raised when a source row has no usable order identity."""


@dataclass(frozen=True, slots=True)
class ResolvedOrderIdentity:
    value: str
    source_field: str


def resolve_order_identity(
    *,
    order_no: object = None,
    source_order_no: object = None,
) -> ResolvedOrderIdentity:
    """Select the stable source identity using the governed global priority."""

    normalized_order_no = str(order_no or "").strip()
    if normalized_order_no:
        return ResolvedOrderIdentity(
            value=normalized_order_no,
            source_field="order_no",
        )

    normalized_source_order_no = str(source_order_no or "").strip()
    if normalized_source_order_no:
        return ResolvedOrderIdentity(
            value=normalized_source_order_no,
            source_field="source_order_no",
        )

    raise OrderIdentityError("订单编号和来源单号均为空")


def resolve_parsed_source_identity(source: ParsedSource) -> ResolvedOrderIdentity:
    return resolve_order_identity(
        order_no=source.order_no,
        source_order_no=source.source_order_no,
    )
