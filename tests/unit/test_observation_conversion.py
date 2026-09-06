from datetime import date

import pytest
from pydantic import ValidationError

from app.models.gsc import NormalizedGSCRow
from app.models.observations import DataStatus
from app.normalization.observation import rows_to_observations


def make_row(
    *,
    impressions: int = 100,
    clicks: int = 10,
    position: float | None = 5.0,
) -> NormalizedGSCRow:
    return NormalizedGSCRow(
        site_id="site-1",
        date=date(2026, 9, 5),
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        impressions=impressions,
        clicks=clicks,
        avg_position=position,
    )


def test_row_is_converted_to_daily_observation():
    observations = rows_to_observations(
        [make_row()]
    )

    assert len(observations) == 1

    observation = observations[0]

    assert observation.site_id == "site-1"
    assert observation.normalized_url == "https://example.com/page"
    assert observation.normalized_query == "کفش مردانه"
    assert observation.date == date(2026, 9, 5)
    assert observation.impressions == 100
    assert observation.clicks == 10
    assert observation.avg_position == 5.0
    assert observation.data_status == DataStatus.OBSERVED


def test_missing_position_is_preserved():
    observations = rows_to_observations(
        [make_row(position=None)]
    )

    assert observations[0].avg_position is None
    assert observations[0].data_status == DataStatus.OBSERVED


def test_multiple_rows_are_preserved():
    first = make_row()
    second = NormalizedGSCRow(
        site_id="site-1",
        date=date(2026, 9, 5),
        normalized_url="https://example.com/other",
        normalized_query="کفش زنانه",
        impressions=200,
        clicks=20,
        avg_position=7.0,
    )

    observations = rows_to_observations(
        [first, second]
    )

    assert len(observations) == 2
    assert observations[0].normalized_query == "کفش مردانه"
    assert observations[1].normalized_query == "کفش زنانه"


def test_empty_rows_return_empty_list():
    assert rows_to_observations([]) == []


def test_domain_validation_rejects_clicks_above_impressions():
    row = make_row(
        impressions=10,
        clicks=20,
    )

    with pytest.raises(ValidationError):
        rows_to_observations([row])