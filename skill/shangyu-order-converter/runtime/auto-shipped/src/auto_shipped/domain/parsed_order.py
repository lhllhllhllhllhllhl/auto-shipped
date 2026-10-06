from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedSource:
    profile_id: str
    source_label: str
    order_type: str
    file_name: str
    file_fingerprint: str
    sheet_name: str
    row_numbers: tuple[int, ...]
    source_order_no: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedRecipient:
    name: str
    mobile: str = ""
    phone: str = ""
    province: str = ""
    city: str = ""
    district: str = ""
    detail: str = ""
    raw_address: str = ""

    @property
    def full_address(self) -> str:
        if self.raw_address.strip():
            return self.raw_address.strip()
        parts = [self.province, self.city, self.district, self.detail]
        return "".join(part.strip() for part in parts if part and part.strip())


@dataclass(frozen=True, slots=True)
class ParsedItem:
    quantity: float
    source_product_code: str = ""
    source_product_name: str = ""
    source_spec: str = ""
    source_display_spec: str = ""
    unit: str = ""
    unit_price: float | None = None
    source_line_no: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedAmounts:
    currency: str = "CNY"
    goods_amount: float | None = None
    freight: float | None = None
    paid_amount: float | None = None


@dataclass(frozen=True, slots=True)
class ParsedShipmentFacts:
    carrier_name: str = ""
    tracking_no: str = ""


@dataclass(slots=True)
class ParsedOrder:
    record_key: str
    source: ParsedSource
    recipient: ParsedRecipient
    items: list[ParsedItem]
    ordered_at: str | None = None
    paid_at: str | None = None
    buyer_reference: str | None = None
    buyer_message: str | None = None
    source_note: str | None = None
    amounts: ParsedAmounts | None = None
    shipment_facts: ParsedShipmentFacts | None = None
    source_extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
