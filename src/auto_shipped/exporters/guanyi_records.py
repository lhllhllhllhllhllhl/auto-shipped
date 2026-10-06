from __future__ import annotations

from dataclasses import dataclass

from auto_shipped.domain import CanonicalOrder


class BlockedOrderError(ValueError):
    """Raised when an unresolved order is passed to an execution-facing exporter."""


@dataclass(frozen=True, slots=True)
class GuanyiRecords:
    main: dict[str, object]
    details: list[dict[str, object]]


def _full_address(order: CanonicalOrder) -> str:
    parts = [
        order.recipient.province,
        order.recipient.city,
        order.recipient.district,
        order.recipient.detail,
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def build_guanyi_records(order: CanonicalOrder) -> GuanyiRecords:
    if order.review_status != "ready":
        codes = ", ".join(sorted({issue.code for issue in order.issues}))
        raise BlockedOrderError(f"订单不是可导出状态: {order.review_status}; issues={codes}")

    main = {
        "平台单号": order.platform_order_no,
        "买家会员名（必填）": order.buyer_member,
        "运费": order.freight,
        "支付金额（必填）": order.payment_amount,
        "订单状态（必填）": order.order_status,
        "买家留言": order.buyer_message,
        "收货人（必填）": order.recipient.name,
        "收货地址（必填）": _full_address(order),
        "联系电话 ": "",
        "联系手机": order.recipient.mobile,
        "订单创建时间": order.source.created_at or "",
        "订单付款时间": order.source.created_at or "",
        "物流单号 ": order.tracking_no,
        "物流公司": order.carrier_name,
        "卖家备注": order.seller_remark,
        "店铺名称（必填）": order.store_name,
        "发票抬头": "",
        "是否手机订单": 0,
        "是否货到付款": 0,
        "支付方式": order.payment_method,
        "支付交易号": "",
        "真实姓名": "",
        "身份证号": "",
        "仓库名称": order.warehouse_name,
        "订单类型": order.order_type,
        "是否分销商订单": 0,
    }
    details = [
        {
            "平台单号": order.platform_order_no,
            "标题": item.title,
            "价格（必填）": item.unit_price,
            "购买数量（必填）": item.quantity,
            "商品代码（必填）": item.product_code,
            # The Guanyi import dialog allows either spec name or spec code.
            # A unique spec code is deterministic and avoids fuzzy matching.
            "规格名称": item.spec_code or item.spec_name or "",
            "是否赠品": 1 if item.is_gift else 0,
            "备注": "",
        }
        for item in order.items
    ]
    return GuanyiRecords(main=main, details=details)

