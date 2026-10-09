from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


MAINLAND_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
PLATFORM_HIDDEN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}-\d{1,6}$")
LANDLINE_RE = re.compile(r"^(?:0\d{2,3}-?\d{7,8}|\d{7,8})(?:-\d{1,6})?$")
SCIENTIFIC_IDENTIFIER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")


def normalize_contact(value: Any) -> tuple[str, str] | None:
    """Return ``(mobile, phone)`` for a supported recipient contact value."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    compact = re.sub(r"\s+", "", text).replace("—", "-").replace("–", "-")
    if MAINLAND_MOBILE_RE.fullmatch(compact):
        return compact, ""
    if PLATFORM_HIDDEN_MOBILE_RE.fullmatch(compact):
        return compact, ""
    if LANDLINE_RE.fullmatch(compact):
        return "", compact
    return None


def identifier_requires_text(value: Any) -> bool:
    """Detect source identifiers that may already have lost Excel precision."""

    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, int):
        return len(str(abs(value))) > 15
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return True
        return len(str(abs(int(value)))) > 15
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return bool(SCIENTIFIC_IDENTIFIER_RE.fullmatch(text))
