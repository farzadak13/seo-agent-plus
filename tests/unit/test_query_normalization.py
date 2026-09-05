import pytest

from app.normalization.query import normalize_query


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Arabic -> Persian characters
        ("ي", "ی"),
        ("ى", "ی"),
        ("كفش", "کفش"),
        ("يك كفش", "یک کفش"),

        # Whitespace
        ("  کفش  مردانه  ", "کفش مردانه"),
        ("کفش\tمردانه", "کفش مردانه"),
        ("کفش\nمردانه", "کفش مردانه"),

        # Diacritics
        ("كِفشِ مَرْدانه", "کفش مردانه"),
        ("مُد و فَشن", "مد و فشن"),

        # Invisible direction characters
        ("\u200eکفش\u200f", "کفش"),
        ("\u061cکفش", "کفش"),

        # Zero-width noise
        ("کفش\u200bمردانه", "کفشمردانه"),

        # ZWNJ
        ("می‌رود", "می‌رود"),
        ("می\u200cرود", "می‌رود"),
        ("می \u200c رود", "می‌رود"),

        # Trimming
        ("\t  کفش مردانه \n", "کفش مردانه"),
    ],
)
def test_normalize_query(raw: str, expected: str) -> None:
    assert normalize_query(raw) == expected


def test_equivalent_persian_queries_normalize_identically() -> None:
    query_a = "كفش  مردانه"
    query_b = "کفش\tمردانه"

    assert normalize_query(query_a) == normalize_query(query_b)


def test_zwnj_is_preserved() -> None:
    assert normalize_query("می‌رود") == "می‌رود"


def test_semantic_spelling_is_not_changed() -> None:
    """
    Normalization must not become spell correction.
    """
    assert normalize_query("مدرسة") == "مدرسة"


def test_persian_and_arabic_kaf_are_equivalent() -> None:
    assert normalize_query("كفش") == normalize_query("کفش")


def test_persian_and_arabic_yeh_are_equivalent() -> None:
    assert normalize_query("يك") == normalize_query("یک")


def test_none_is_rejected() -> None:
    with pytest.raises(TypeError):
        normalize_query(None)  # type: ignore[arg-type]


def test_non_string_is_rejected() -> None:
    with pytest.raises(TypeError):
        normalize_query(123)  # type: ignore[arg-type]


def test_empty_string_is_allowed() -> None:
    assert normalize_query("") == ""


def test_whitespace_only_becomes_empty() -> None:
    assert normalize_query("   \t\n ") == ""