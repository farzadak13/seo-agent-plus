from datetime import date, datetime, timezone

import pytest

from app.models.gsc import RawGSCResponse
from app.normalization.gsc import normalize_gsc_response


def make_response(rows):
    return RawGSCResponse(
        response_id="response-001",
        site_id="site-1",
        fetch_date=date(2026, 9, 5),
        raw_payload={"rows": rows},
        created_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )


def test_normalizes_url_and_query():
    response = make_response(
        [
            {
                "keys": [
                    "HTTP://EXAMPLE.COM/page?utm_source=x",
                    "كِفش  مردانه",
                ],
                "clicks": 10,
                "impressions": 100,
                "position": 4.5,
            }
        ]
    )

    rows = normalize_gsc_response(response)

    assert len(rows) == 1

    row = rows[0]

    assert row.site_id == "site-1"
    assert row.date == date(2026, 9, 5)
    assert row.normalized_url == "https://example.com/page"
    assert row.normalized_query == "کفش مردانه"
    assert row.clicks == 10
    assert row.impressions == 100
    assert row.avg_position == 4.5


def test_normalizer_preserves_meaningful_query_parameters():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page?id=10&utm_source=x",
                    "کفش مردانه",
                ],
                "clicks": 5,
                "impressions": 50,
                "position": 3,
            }
        ]
    )

    rows = normalize_gsc_response(response)

    assert rows[0].normalized_url == (
        "https://example.com/page?id=10"
    )


def test_missing_position_is_allowed():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "clicks": 0,
                "impressions": 100,
                "position": None,
            }
        ]
    )

    rows = normalize_gsc_response(response)

    assert rows[0].avg_position is None


def test_empty_rows_return_empty_list():
    response = make_response([])

    assert normalize_gsc_response(response) == []


def test_invalid_rows_container_is_rejected():
    response = RawGSCResponse(
        response_id="response-002",
        site_id="site-1",
        fetch_date=date(2026, 9, 5),
        raw_payload={"rows": "invalid"},
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValueError):
        normalize_gsc_response(response)


def test_missing_keys_are_rejected():
    response = make_response(
        [
            {
                "clicks": 10,
                "impressions": 100,
            }
        ]
    )

    with pytest.raises(ValueError):
        normalize_gsc_response(response)


def test_invalid_metrics_are_rejected():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "clicks": "10",
                "impressions": 100,
                "position": 4.0,
            }
        ]
    )

    with pytest.raises(ValueError):
        normalize_gsc_response(response)


def test_non_object_row_is_rejected():
    response = make_response(
        [
            "invalid-row",
        ]
    )

    with pytest.raises(ValueError):
        normalize_gsc_response(response)


def test_position_is_converted_to_float():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "clicks": 10,
                "impressions": 100,
                "position": 4,
            }
        ]
    )

    rows = normalize_gsc_response(response)

    assert rows[0].avg_position == 4.0
    assert isinstance(rows[0].avg_position, float)


def test_semantic_query_spelling_is_not_changed():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "مدرسة",
                ],
                "clicks": 1,
                "impressions": 10,
                "position": 10,
            }
        ]
    )

    rows = normalize_gsc_response(response)

    assert rows[0].normalized_query == "مدرسة"