from datetime import date, datetime, timezone

import pytest

from app.ingestion.gsc import ingest_gsc_response
from app.models.gsc import RawGSCResponse
from app.models.observations import DataStatus


def make_response(rows: list[dict]) -> RawGSCResponse:
    return RawGSCResponse(
        response_id="response-1",
        site_id="site-1",
        fetch_date=date(2026, 9, 5),
        raw_payload={
            "rows": rows,
        },
        created_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )


def test_ingestion_returns_normalized_rows():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 10,
                "position": 5.0,
            }
        ]
    )

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert len(result.normalized_rows) == 1
    assert result.normalized_rows[0].normalized_url == (
        "https://example.com/page"
    )


def test_ingestion_returns_observations():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 10,
                "position": 5.0,
            }
        ]
    )

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert len(result.observations) == 1
    assert result.observations[0].site_id == "site-1"
    assert result.observations[0].impressions == 100
    assert result.observations[0].clicks == 10


def test_ingestion_classifies_observed_day():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 10,
                "position": 5.0,
            }
        ]
    )

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert result.daily_status == [
        (date(2026, 9, 5), DataStatus.OBSERVED)
    ]


def test_ingestion_detects_missing_unknown_day():
    response = make_response([])

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert result.daily_status == [
        (date(2026, 9, 5), DataStatus.MISSING_UNKNOWN)
    ]


def test_ingestion_supports_expected_zero_day():
    response = make_response([])

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
        expected_zero_dates=[
            date(2026, 9, 5),
        ],
    )

    assert result.daily_status == [
        (date(2026, 9, 5), DataStatus.MISSING_EXPECTED_ZERO)
    ]


def test_ingestion_preserves_site_and_response_identity():
    response = make_response([])

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert result.site_id == "site-1"
    assert result.response_id == "response-1"


def test_ingestion_is_deterministic():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 10,
                "position": 5.0,
            }
        ]
    )

    first = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    second = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert first.model_dump() == second.model_dump()


def test_ingestion_rejects_invalid_gsc_data():
    response = make_response(
        [
            {
                "keys": [
                    "not-a-valid-url",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 10,
                "position": 5.0,
            }
        ]
    )

    with pytest.raises(ValueError):
        ingest_gsc_response(
            response=response,
            start_date=date(2026, 9, 5),
            end_date=date(2026, 9, 5),
        )

def test_ingestion_returns_url_daily_metrics():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 10,
                "position": 5.0,
            },
            {
                "keys": [
                    "https://example.com/page",
                    "کفش ورزشی",
                ],
                "impressions": 50,
                "clicks": 5,
                "position": 6.0,
            },
        ]
    )

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert len(result.url_daily_metrics) == 1

    metric = result.url_daily_metrics[0]

    assert metric.site_id == "site-1"
    assert metric.normalized_url == "https://example.com/page"
    assert metric.date == date(2026, 9, 5)
    assert metric.total_impressions == 150
    assert metric.total_clicks == 15


def test_ingestion_separates_query_level_and_url_level_metrics():
    response = make_response(
        [
            {
                "keys": [
                    "https://example.com/page",
                    "کفش مردانه",
                ],
                "impressions": 100,
                "clicks": 10,
                "position": 5.0,
            },
            {
                "keys": [
                    "https://example.com/page",
                    "کفش ورزشی",
                ],
                "impressions": 50,
                "clicks": 5,
                "position": 6.0,
            },
        ]
    )

    result = ingest_gsc_response(
        response=response,
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 5),
    )

    assert len(result.normalized_rows) == 2
    assert len(result.observations) == 2
    assert len(result.url_daily_metrics) == 1

    assert sum(
        observation.impressions
        for observation in result.observations
    ) == 150

    assert result.url_daily_metrics[0].total_impressions == 150