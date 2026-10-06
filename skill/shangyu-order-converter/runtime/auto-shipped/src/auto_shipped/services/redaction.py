from __future__ import annotations

from copy import deepcopy

from auto_shipped.domain import CanonicalOrder


def _mask_name(value: str) -> str:
    if not value:
        return ""
    if len(value) == 1:
        return "*"
    return value[0] + "*" * (len(value) - 1)


def _mask_mobile(value: str) -> str:
    if len(value) == 11:
        return value[:3] + "****" + value[-4:]
    return "<invalid-or-missing>" if value else ""


def redact_order(order: CanonicalOrder) -> dict:
    data = deepcopy(order.to_dict())
    recipient = data["recipient"]
    recipient["name"] = _mask_name(recipient["name"])
    recipient["mobile"] = _mask_mobile(recipient["mobile"])
    recipient["detail"] = f"<已脱敏:{len(recipient['detail'])}字符>" if recipient["detail"] else ""
    return data

