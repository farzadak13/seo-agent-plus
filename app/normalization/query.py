from __future__ import annotations

import re
import unicodedata


# Arabic/Persian character normalization.
# فقط کاراکترهایی را تغییر می‌دهیم که تبدیلشان قطعی و غیرمعنایی است.
CHARACTER_TRANSLATION = str.maketrans(
    {
        "\u064a": "\u06cc",  # ي -> ی
        "\u0649": "\u06cc",  # ى -> ی
        "\u0643": "\u06a9",  # ك -> ک
    }
)

# Directional formatting / invisible control characters.
INVISIBLE_CONTROL_CHARS_RE = re.compile(
    r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)

# Zero-width characters that are noise for our identity system.
# ZWNJ (\u200c) is intentionally preserved.
ZERO_WIDTH_NOISE_RE = re.compile(
    r"[\u200b\u200d\ufeff]"
)

# Arabic diacritics / harakat.
DIACRITICS_RE = re.compile(
    r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]"
)

# Any Unicode whitespace sequence -> one normal space.
WHITESPACE_RE = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    """
    Normalize a search query for stable identity/comparison.

    This function is intentionally conservative:
    it normalizes characters and formatting but does not perform
    semantic spelling correction.

    Examples:
        "  كِفش  مردانه  " -> "کفش مردانه"
        "مي رود"            -> "می رود"
        "مي‌رود"            -> "می‌رود"
    """

    if not isinstance(query, str):
        raise TypeError("query must be a string")

    value = query

    # 1. Canonical Unicode composition.
    value = unicodedata.normalize("NFC", value)

    # 2. Normalize Arabic/Persian character variants.
    value = value.translate(CHARACTER_TRANSLATION)

    # 3. Remove directional control characters.
    value = INVISIBLE_CONTROL_CHARS_RE.sub("", value)

    # 4. Remove zero-width noise, but preserve ZWNJ.
    value = ZERO_WIDTH_NOISE_RE.sub("", value)

    # 5. Remove Arabic/Persian diacritics.
    value = DIACRITICS_RE.sub("", value)

    # 6. Normalize all whitespace sequences.
    value = WHITESPACE_RE.sub(" ", value).strip()

    # 7. Canonicalize whitespace around ZWNJ.
    #    "می ‌رود" -> "می‌رود"
    value = re.sub(r"\s*\u200c\s*", "\u200c", value)

    return value