from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.gsc import NormalizedGSCRow, RawGSCResponse


def test_valid_raw_gsc_response():
    response = RawGSCResponse(
        response_id="response-001",
        site_id="site-1",
        fetch_date=date(2026, 9, 5),
        raw_payload={
            "rows": [
                {
                    "keys": [
                        "https://example.com/page",
                        "کفش مردانه",
                    ],
                    "clicks": 10,
                    "impressions": 100,
                    "position": 4.2,
                }
            ]
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

    assert response.response_id == "response-001"
    assert response.site_id == "site-1"
    assert response.raw_payload["rows"][0]["clicks"] == 10


def test_raw_payload_can_contain_arbitrary_gsc_data():
    response = RawGSCResponse(
        response_id="response-002",
        site_id="site-1",
        fetch_date=date(2026, 9, 5),
        raw_payload={
            "rows": [],
            "metadata": {
                "api_version": "v1",
                "extra": ["a", "b"],
            },
        },
        created_at=datetime.now(timezone.utc),
    )

    assert response.raw_payload["metadata"]["extra"] == ["a", "b"]


def test_empty_response_id_is_rejected():
    with pytest.raises(ValidationError):
        RawGSCResponse(
            response_id="",
            site_id="site-1",
            fetch_date=date(2026, 9, 5),
            raw_payload={},
            created_at=datetime.now(timezone.utc),
        )


def test_extra_raw_response_fields_are_rejected():
    with pytest.raises(ValidationError):
        RawGSCResponse(
            response_id="response-003",
            site_id="site-1",
            fetch_date=date(2026, 9, 5),
            raw_payload={},
            created_at=datetime.now(timezone.utc),
            unexpected_field="bad",
        )


def test_valid_normalized_gsc_row():
    row = NormalizedGSCRow(
        site_id="site-1",
        date=date(2026, 9, 4),
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        impressions=1000,
        clicks=50,
        avg_position=5.2,
    )

    assert row.impressions == 1000
    assert row.clicks == 50
    assert row.avg_position == 5.2


def test_normalized_gsc_row_allows_missing_position():
    row = NormalizedGSCRow(
        site_id="site-1",
        date=date(2026, 9, 4),
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        impressions=100,
        clicks=5,
        avg_position=None,
    )

    assert row.avg_position is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("impressions", -1),
        ("clicks", -1),
        ("avg_position", -0.1),
    ],
)
def test_normalized_gsc_row_rejects_negative_metrics(field, value):
    data = {
        "site_id": "site-1",
        "date": date(2026, 9, 4),
        "normalized_url": "https://example.com/page",
        "normalized_query": "کفش مردانه",
        "impressions": 100,
        "clicks": 5,
        "avg_position": 5.2,
    }

    data[field] = value

    with pytest.raises(ValidationError):
        NormalizedGSCRow(**data)


def test_extra_normalized_gsc_row_fields_are_rejected():
    with pytest.raises(ValidationError):
        NormalizedGSCRow(
            site_id="site-1",
            date=date(2026, 9, 4),
            normalized_url="https://example.com/page",
            normalized_query="کفش مردانه",
            impressions=100,
            clicks=5,
            avg_position=5.2,
            unexpected_field="bad",
        )