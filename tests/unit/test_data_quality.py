from datetime import date

from app.features.data_quality import calculate_window_completeness
from app.models.observations import DataStatus


def test_complete_window_has_full_completeness():
    statuses = [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.OBSERVED),
        (date(2026, 9, 3), DataStatus.OBSERVED),
        (date(2026, 9, 4), DataStatus.OBSERVED),
        (date(2026, 9, 5), DataStatus.OBSERVED),
    ]

    assert calculate_window_completeness(statuses) == 1.0


def test_partial_window_uses_calendar_denominator():
    statuses = [
        (date(2026, 9, 1), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 2), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 3), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 4), DataStatus.MISSING_UNKNOWN),
        (date(2026, 9, 5), DataStatus.OBSERVED),
    ]

    assert calculate_window_completeness(statuses) == 0.2


def test_expected_zero_counts_as_usable():
    statuses = [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.MISSING_EXPECTED_ZERO),
        (date(2026, 9, 3), DataStatus.OBSERVED),
        (date(2026, 9, 4), DataStatus.OBSERVED),
    ]

    assert calculate_window_completeness(statuses) == 1.0


def test_unknown_days_reduce_completeness():
    statuses = [
        (date(2026, 9, 1), DataStatus.OBSERVED),
        (date(2026, 9, 2), DataStatus.MISSING_UNKNOWN),
    ]

    assert calculate_window_completeness(statuses) == 0.5