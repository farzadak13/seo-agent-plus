"""Iranian mobile numbers, written one way.

People type them every way there is: 0912…, +98912…, 0098912…, 912…, with
Persian or Arabic digits, spaces and dashes. All of them are one person, so
all of them become 09XXXXXXXXX before anything is looked up or sent.
"""
from __future__ import annotations

import re

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MOBILE = re.compile(r"^09[0-9]{9}$")


def to_ascii_digits(text: str) -> str:
    return text.translate(_DIGITS)


def normalize_mobile(text: str | None) -> str | None:
    """09XXXXXXXXX, or None when this is not an Iranian mobile number."""
    if not text:
        return None
    digits = re.sub(r"[\s\-()]", "", text.translate(_DIGITS))
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("0098"):
        digits = "0" + digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    return digits if _MOBILE.match(digits) else None


def masked(mobile: str) -> str:
    """For logs and screens: 0912***4567."""
    return f"{mobile[:4]}***{mobile[-4:]}"
