from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.observations import (
    CalendarContext,
    DailyObservation,
    DataStatus,
)


def test_valid_observation():
    observation = DailyObservation(
        site_id="site_001",
        normalized_url="https://example.com/mens-shoes/",
        normalized_query="خرید کفش اسپرت مردانه",
        date=date(2026, 8, 25),
        impressions=1000,
        clicks=50,
        avg_position=4.2,
        data_status=DataStatus.OBSERVED,
        calendar_context=CalendarContext(
            is_weekend=False,
            holiday_overlap=False,
        ),
        retrieved_at=datetime(
            2026,
            8,
            28,
            10,
            30,
            tzinfo=timezone.utc,
        ),
    )

    assert observation.site_id == "site_001"
    assert observation.impressions == 1000
    assert observation.clicks == 50
    assert observation.avg_position == 4.2
    assert observation.data_status == DataStatus.OBSERVED
    assert observation.ctr() == 0.05


def test_zero_impressions_produce_zero_ctr():
    observation = DailyObservation(
        site_id="site_001",
        normalized_url="https://example.com/page/",
        normalized_query="کفش",
        date=date(2026, 8, 25),
        impressions=0,
        clicks=0,
        avg_position=None,
        data_status=DataStatus.MISSING_EXPECTED_ZERO,
    )

    assert observation.ctr() == 0.0


def test_blank_site_id_is_rejected():
    with pytest.raises(ValidationError):
        DailyObservation(
            site_id="   ",
            normalized_url="https://example.com/page/",
            normalized_query="کفش",
            date=date(2026, 8, 25),
            impressions=10,
            clicks=1,
            data_status=DataStatus.OBSERVED,
        )


def test_negative_impressions_are_rejected():
    with pytest.raises(ValidationError):
        DailyObservation(
            site_id="site_001",
            normalized_url="https://example.com/page/",
            normalized_query="کفش",
            date=date(2026, 8, 25),
            impressions=-1,
            clicks=0,
            data_status=DataStatus.OBSERVED,
        )


def test_negative_clicks_are_rejected():
    with pytest.raises(ValidationError):
        DailyObservation(
            site_id="site_001",
            normalized_url="https://example.com/page/",
            normalized_query="کفش",
            date=date(2026, 8, 25),
            impressions=100,
            clicks=-1,
            data_status=DataStatus.OBSERVED,
        )


def test_clicks_cannot_exceed_impressions():
    with pytest.raises(ValidationError):
        DailyObservation(
            site_id="site_001",
            normalized_url="https://example.com/page/",
            normalized_query="کفش",
            date=date(2026, 8, 25),
            impressions=10,
            clicks=11,
            data_status=DataStatus.OBSERVED,
        )


def test_avg_position_can_be_none():
    observation = DailyObservation(
        site_id="site_001",
        normalized_url="https://example.com/page/",
        normalized_query="کفش",
        date=date(2026, 8, 25),
        impressions=0,
        clicks=0,
        avg_position=None,
        data_status=DataStatus.MISSING_UNKNOWN,
    )

    assert observation.avg_position is None


def test_default_calendar_context():
    observation = DailyObservation(
        site_id="site_001",
        normalized_url="https://example.com/page/",
        normalized_query="کفش",
        date=date(2026, 8, 25),
        impressions=10,
        clicks=1,
        data_status=DataStatus.OBSERVED,
    )

    assert observation.calendar_context.is_weekend is False
    assert observation.calendar_context.holiday_overlap is False
    assert observation.calendar_context.event_type is None


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        DailyObservation(
            site_id="site_001",
            normalized_url="https://example.com/page/",
            normalized_query="کفش",
            date=date(2026, 8, 25),
            impressions=10,
            clicks=1,
            data_status=DataStatus.OBSERVED,
            something_we_did_not_define="bad",
        )