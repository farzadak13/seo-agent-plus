from datetime import date

import pytest

from app.ingestion.calendar import expand_date_range


def test_expand_single_day():
    result = expand_date_range(
        date(2026, 9, 5),
        date(2026, 9, 5),
    )

    assert result == [
        date(2026, 9, 5),
    ]


def test_expand_multiple_days_inclusive():
    result = expand_date_range(
        date(2026, 9, 1),
        date(2026, 9, 5),
    )

    assert result == [
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
        date(2026, 9, 4),
        date(2026, 9, 5),
    ]


def test_expand_range_preserves_chronological_order():
    result = expand_date_range(
        date(2026, 9, 3),
        date(2026, 9, 7),
    )

    assert result == sorted(result)


def test_expand_range_contains_no_duplicates():
    result = expand_date_range(
        date(2026, 9, 1),
        date(2026, 9, 10),
    )

    assert len(result) == len(set(result))


def test_expand_invalid_range_raises_error():
    with pytest.raises(ValueError, match="end_date must be greater than or equal to start_date"):
        expand_date_range(
            date(2026, 9, 10),
            date(2026, 9, 1),
        )


def test_expand_two_day_range():
    result = expand_date_range(
        date(2026, 9, 1),
        date(2026, 9, 2),
    )

    assert result == [
        date(2026, 9, 1),
        date(2026, 9, 2),
    ]


def test_expand_one_week_returns_seven_days():
    result = expand_date_range(
        date(2026, 9, 1),
        date(2026, 9, 7),
    )

    assert len(result) == 7