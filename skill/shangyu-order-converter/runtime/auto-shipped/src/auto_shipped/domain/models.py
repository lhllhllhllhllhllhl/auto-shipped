from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field
from typing import Any, Literal


Severity = Literal["info", "warning", "error"]
MappingStatus = Literal["confirmed", "unconfirmed", "unmapped", "ambiguous"]


@dataclass(slots=True)
class Issue:
    code: str
    message: str
    severity: Severity = "error"
    field: str | None = None
    source_ref: str | None = None
    details: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(slots=True)
class SourceRef:
    profile_id: str
    source_file: str
    source_order_id: str
    row_ref: str
    company: str = ""
    created_at: str | None = None


@dataclass(slots=True)
class Recipient:
    name: str
    mobile: str
    province: str
    city: str
    district: str
    detail: str


@dataclass(slots=True)
class OrderItem:
    external_sku: str
    title: str
    quantity: float
    unit_price: float = 0.0
    product_code: str | None = None
    spec_code: str | None = None
    spec_name: str | None = None
    weight_grams: float | None = None
    is_gift: bool = False
    mapping_status: MappingStatus = "unmapped"


@dataclass(slots=True)
class CanonicalOrder:
    order_id: str
    source: SourceRef
    platform_order_no: str
    buyer_member: str
    store_name: str
    warehouse_name: str
    recipient: Recipient
    items: list[OrderItem]
    order_status: str = "买家已付款，等待卖家发货"
    order_type: str = "销售订单"
    payment_method: str = "网银在线"
    payment_amount: float = 0.0
    freight: float = 0.0
    buyer_message: str = ""
    seller_remark: str = ""
    logistics_policy: str = "rules_engine"
    carrier_name: str = ""
    tracking_no: str = ""
    issues: list[Issue] = dc_field(default_factory=list)
    metadata: dict[str, Any] = dc_field(default_factory=dict)
    schema_version: str = "1.0"

    @property
    def review_status(self) -> str:
        if any(issue.severity == "error" for issue in self.issues):
            return "blocked"
        if any(issue.severity == "warning" for issue in self.issues):
            return "needs_review"
        return "ready"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["review_status"] = self.review_status
        return result
