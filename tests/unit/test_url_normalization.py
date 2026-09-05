import pytest

from app.normalization.url import canonicalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "http://example.com/page",
            "https://example.com/page",
        ),
        (
            "https://EXAMPLE.COM/Page",
            "https://example.com/Page",
        ),
        (
            "https://example.com/page#section",
            "https://example.com/page",
        ),
        (
            "https://example.com/page?utm_source=instagram",
            "https://example.com/page",
        ),
        (
            "https://example.com/page?utm_source=x&utm_medium=social&id=10",
            "https://example.com/page?id=10",
        ),
        (
            "https://example.com/page?b=2&a=1",
            "https://example.com/page?a=1&b=2",
        ),
        (
            "https://example.com/page/",
            "https://example.com/page/",
        ),
        (
            "https://example.com//products///shoes",
            "https://example.com/products/shoes",
        ),
        (
            "https://example.com/page?fbclid=abc&color=red",
            "https://example.com/page?color=red",
        ),
        (
            "https://www.example.com/page",
            "https://www.example.com/page",
        ),
        (
            "http://example.com:80/page",
            "https://example.com/page",
        ),
        (
            "https://example.com:443/page",
            "https://example.com/page",
        ),
        (
            "https://example.com:8443/page",
            "https://example.com:8443/page",
        ),
    ],
)
def test_canonicalize_url(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_non_tracking_query_parameters_are_preserved() -> None:
    raw = "https://example.com/product?id=123&color=black"

    result = canonicalize_url(raw)

    assert result == "https://example.com/product?color=black&id=123"


def test_tracking_parameters_are_case_insensitive() -> None:
    raw = "https://example.com/page?UTM_Source=google&id=123"

    assert canonicalize_url(raw) == "https://example.com/page?id=123"


def test_root_url_is_preserved() -> None:
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_empty_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_url("")


def test_relative_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_url("/products/shoes")


def test_non_http_scheme_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_url("ftp://example.com/file")


def test_missing_hostname_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_url("https:///page")


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_url("https://example.com:abc/page")


def test_non_string_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        canonicalize_url(None)  # type: ignore[arg-type]


def test_fragment_never_affects_identity() -> None:
    url_a = "https://example.com/page#one"
    url_b = "https://example.com/page#two"

    assert canonicalize_url(url_a) == canonicalize_url(url_b)