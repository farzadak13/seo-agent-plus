from datetime import date

from app.ingestion.daily_window import classify_daily_window
from app.models.observations import DataStatus, DailyObservation


def make_observation(day: int) -> DailyObservation:
    return DailyObservation(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=date(2026, 9, day),
        impressions=100,
        clicks=10,
        avg_position=5.0,
        data_status=DataStatus.OBSERVED,
    )


def test_daily_window_classifies_complete_range():
    result = classify_daily_window(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        observations=[
            make_observation(1),
            make_observation(2),
            make_observation(3),
        ],
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.OBSERVED),
        (date(2026, 9, 3), DataStatus.OBSERVED),
    ]


def test_daily_window_detects_missing_day():
    result = classify_daily_window(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        observations=[
            make_observation(1),
            make_observation(3),
        ],
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 3), DataStatus.OBSERVED),
    ]


def test_daily_window_supports_expected_zero_dates():
    result = classify_daily_window(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        observations=[
            make_observation(1),
            make_observation(3),
        ],
        expected_zero_dates=[
            date(2026, 9, 2),
        ],
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.MISSING_EXPECTED_ZERO),
        (date(2026, 9, 3), DataStatus.OBSERVED),
    ]


def test_daily_window_does_not_mutate_observations():
    observations = [
        make_observation(1),
    ]

    classify_daily_window(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        observations=observations,
    )

    assert len(observations) == 1
    assert observations[0].data_status == DataStatus.OBSERVED


def test_daily_window_returns_dates_in_order():
    result = classify_daily_window(
        start_date=date(2026, 9, 5),
        end_date=date(2026, 9, 9),
        observations=[],
    )

    assert [day for day, _ in result] == [
        date(2026, 9, 5),
        date(2026, 9, 6),
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
    ]


def test_daily_window_invalid_range_propagates_error():
    import pytest

    with pytest.raises(
        ValueError,
        match="end_date must be greater than or equal to start_date",
    ):
        classify_daily_window(
            start_date=date(2026, 9, 5),
            end_date=date(2026, 9, 1),
            observations=[],
        )


def test_daily_window_empty_observations_make_missing_days_unknown():
    result = classify_daily_window(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 3),
        observations=[],
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 2), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 3), DataStatus.MISSING_UNKNOWN),
    ]


def test_observation_on_date_outside_requested_window_is_ignored():
    result = classify_daily_window(
        start_date=date(2026, 9, 2),
        end_date=date(2026, 9, 3),
        observations=[
            make_observation(1),
        ],
    )

    assert result == [
        (date(2026, 9, 2), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 3), DataStatus.MISSING_UNKNOWN),
    ]