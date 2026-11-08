"""Promises a model may put in a title or description that the page never made.

Cheaper models write good, fluent Persian, and add what sells: a discount, free
delivery, "the best clinic", booking online. On a customer's site that is a
promise to their customers they may not keep, so a text that makes one the
page's own words do not is not shown at all. A plain list, read the same way
for every model, so what the customer sees does not depend on which one wrote it.
"""
from __future__ import annotations

import re

from app.normalization.query import normalize_query

# Each one as it reads with spaces and half-spaces taken out (see _found_in), and
# digits written the Persian way. Phrases, not bare words, where the word alone
# is everyday Persian: نوبت ("twice a day"), قطعی ("a power cut").
_PROMISES = (
    # price and sales
    "تخفیف", "حراج", "جشنواره", "فروشویژه", "شرایطویژه", "پیشنهادویژه",
    "ارزان", "ارزون", "قیمتمناسب", "%", "٪",
    "اقساط", "قسطی", "پرداختدرمحل",
    # free
    "رایگان", "مجانی",
    # guarantees
    "ضمانت", "گارانتی", "تضمین", "بازگشتوجه", "مرجوع", "اصالت", "اورجینال",
    # delivery
    "ارسالفوری", "ارسالسریع", "ارسالرایگان", "ارسالامروز", "ارسالهمانروز", "ارسالیکروزه",
    "تحویلفوری", "تحویلسریع", "تحویلامروز", "۲۴ساعته", "ارسالبهسراسر",
    # superlatives (never a bare ترین: کترینگ)
    "بهترین", "برترین", "ارزانترین", "ارزونترین", "معتبرترین", "سریعترین", "کاملترین", "مطمئنترین",
    # booking and calls to act
    "نوبتدهی", "نوبتبگیرید", "نوبتبگیر", "گرفتننوبت", "دریافتنوبت", "وقتبگیرید", "رزرو", "مشاورهتلفنی",
    # medical
    "درمانقطعی", "بدوندرد", "بدونعارضه",
)
# "number one", but not plate number 15.
_NUMBER_ONE = re.compile(r"شماره(۱|یک)(?![۰-۹])")
# "20 percent", but not "در صدد" or "در صدر".
_PERCENT = re.compile(r"[۰-۹]درصد")
# Latin ones as whole words: "free" but not "freelance", "off" but not "coffee".
_LATIN = re.compile(r"\b(free|off|sale)\b")

_EXTRA_LETTERS = str.maketrans({"ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ـ": None})
_DIGITS = str.maketrans("0123456789٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹۰۱۲۳۴۵۶۷۸۹")
_HALF_SPACE = "‌"


def _spaced(text: str) -> str:
    # The shared normalizer first: diacritics, zero-width and direction marks.
    return normalize_query(text or "").translate(_EXTRA_LETTERS).translate(_DIGITS).casefold()


def _found_in(text: str | None) -> set[str]:
    spaced = _spaced(text or "")
    read = "".join(spaced.replace(_HALF_SPACE, " ").split())
    found = {promise for promise in _PROMISES if promise in read}
    if _NUMBER_ONE.search(read):
        found.add("شماره۱")
    if _PERCENT.search(read):
        found.add("درصد")
    found |= set(_LATIN.findall(spaced))
    return found


def promises_not_made(text: str, *page_words: str | None) -> list[str]:
    """The promises in text that none of the page's own words make; [] when it makes none."""
    said: set[str] = set()
    for words in page_words:
        said |= _found_in(words)
    return sorted(_found_in(text) - said)
