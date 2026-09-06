from datetime import date

from app.features.missing_data import (
    apply_data_status,
    classify_missing_days,
)
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


def test_observed_dates_are_classified_as_observed():
    observations = [
        make_observation(1),
        make_observation(3),
    ]

    result = classify_missing_days(
        expected_dates=[
            date(2026, 9, 1),
            date(2026, 9, 2),
            date(2026, 9, 3),
        ],
        observations=observations,
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 3), DataStatus.OBSERVED),
    ]


def test_expected_zero_date_is_classified_explicitly():
    observations = [
        make_observation(1),
    ]

    result = classify_missing_days(
        expected_dates=[
            date(2026, 9, 1),
            date(2026, 9, 2),
        ],
        observations=observations,
        expected_zero_dates=[
            date(2026, 9, 2),
        ],
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.MISSING_EXPECTED_ZERO),
    ]


def test_unknown_missing_date_is_not_treated_as_zero():
    result = classify_missing_days(
        expected_dates=[
            date(2026, 9, 1),
        ],
        observations=[],
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.MISSING_UNKNOWN),
    ]


def test_empty_expected_dates_return_empty_result():
    result = classify_missing_days(
        expected_dates=[],
        observations=[],
    )

    assert result == []


def test_results_are_sorted_by_date():
    result = classify_missing_days(
        expected_dates=[
            date(2026, 9, 3),
            date(2026, 9, 1),
            date(2026, 9, 2),
        ],
        observations=[],
    )

    assert result == [
        (date(2026, 9, 1), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 2), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 3), DataStatus.MISSING_UNKNOWN),
    ]


def test_apply_data_status_updates_existing_observations():
    observations = [
        make_observation(1),
        make_observation(2),
    ]

    result = apply_data_status(
        observations,
        {
            date(2026, 9, 1): DataStatus.OBSERVED,
            date(2026, 9, 2): DataStatus.MISSING_EXPECTED_ZERO,
        },
    )

    assert result[0].data_status == DataStatus.OBSERVED
    assert result[1].data_status == DataStatus.MISSING_EXPECTED_ZERO


def test_apply_data_status_does_not_mutate_original_observations():
    observations = [
        make_observation(1),
    ]

    result = apply_data_status(
        observations,
        {
            date(2026, 9, 1): DataStatus.MISSING_EXPECTED_ZERO,
        },
    )

    assert observations[0].data_status == DataStatus.OBSERVED
    assert result[0].data_status == DataStatus.MISSING_EXPECTED_ZERO


def test_partial_status_mapping_preserves_existing_status():
    observations = [
        make_observation(1),
    ]

    result = apply_data_status(
        observations,
        {},
    )

    assert result[0].data_status == DataStatus.OBSERVED

def test_classifier_does_not_create_missing_observations():
    observations = [
        make_observation(1),
    ]

    result = classify_missing_days(
        expected_dates=[
            date(2026, 9, 1),
            date(2026, 9, 2),
            date(2026, 9, 3),
        ],
        observations=observations,
    )

    assert len(observations) == 1
    assert len(result) == 3