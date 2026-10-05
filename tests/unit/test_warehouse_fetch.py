"""One day of Search Console data, fetched, normalised and made additive."""
from datetime import date

import pytest

from app.warehouse.fetch import (
    PAGE_DIMENSIONS,
    QUERY_DIMENSIONS,
    TOTALS_DIMENSIONS,
    FetchError,
    fetch_day,
)
from app.warehouse.models import DAILY_ROW_CAP, Device


DAY = date(2026, 9, 30)
D = DAY.isoformat()


def row(keys, clicks, impressions, position):
    return {"keys": keys, "clicks": clicks, "impressions": impressions, "position": position}


class FakeGSC:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, dimensions, day):
        self.asked.append((dimensions, day))
        return self.answers.get(dimensions, [])


def fetch(answers):
    gsc = FakeGSC(answers)
    return fetch_day(site_id="s1", property_url="sc-domain:tennisino.com", day=DAY, query=gsc), gsc


def test_three_requests_for_the_day_split_by_device():
    data, gsc = fetch({})
    assert [dims for dims, _ in gsc.asked] == [TOTALS_DIMENSIONS, PAGE_DIMENSIONS, QUERY_DIMENSIONS]
    assert all(day == DAY for _, day in gsc.asked)
    assert all("device" in dims for dims, _ in gsc.asked)


def test_position_is_stored_as_a_sum_so_ranges_average_correctly():
    data, _ = fetch({TOTALS_DIMENSIONS: [row([D, "MOBILE"], 10, 200, 3.5)]})
    [total] = data.totals
    assert (total.device, total.clicks, total.impressions) == (Device.MOBILE, 10, 200)
    assert total.position_sum == pytest.approx(700.0)


def test_urls_and_queries_go_through_the_shared_normalisers_and_merge():
    data, _ = fetch({
        PAGE_DIMENSIONS: [
            row([D, "https://Tennisino.com/racket?utm_source=x", "MOBILE"], 1, 10, 2.0),
            row([D, "https://tennisino.com/racket", "MOBILE"], 2, 30, 4.0),
        ],
        QUERY_DIMENSIONS: [
            row([D, "https://tennisino.com/racket", "راكت تنيس", "DESKTOP"], 1, 10, 5.0),  # Arabic kaf/yeh
            row([D, "https://tennisino.com/racket", "راکت تنیس", "DESKTOP"], 3, 20, 2.0),
        ],
    })
    [page] = data.pages
    assert page.url == "https://tennisino.com/racket"
    assert (page.clicks, page.impressions) == (3, 40)
    assert page.position_sum == pytest.approx(10 * 2.0 + 30 * 4.0), "summed, not one kept"
    [query] = data.queries
    assert query.query == "راکت تنیس"
    assert (query.clicks, query.impressions, query.position_sum) == (4, 30, pytest.approx(90.0))


def test_each_device_is_its_own_row():
    data, _ = fetch({TOTALS_DIMENSIONS: [row([D, "MOBILE"], 1, 10, 1.0), row([D, "DESKTOP"], 2, 20, 2.0)]})
    assert {t.device for t in data.totals} == {Device.MOBILE, Device.DESKTOP}


def test_a_row_for_another_day_is_refused():
    with pytest.raises(FetchError, match="2026-09-29"):
        fetch({TOTALS_DIMENSIONS: [row(["2026-09-29", "MOBILE"], 1, 1, 1.0)]})


@pytest.mark.parametrize(
    "bad",
    [
        row([D, "MOBILE"], -1, 1, 1.0),
        row([D, "MOBILE"], 1.5, 1, 1.0),
        row([D, "MOBILE"], 1, 1, -2.0),
        row([D], 1, 1, 1.0),
        row([D, "SMARTWATCH"], 1, 1, 1.0),
    ],
)
def test_rows_that_cannot_be_stored_faithfully_are_refused(bad):
    with pytest.raises(FetchError):
        fetch({TOTALS_DIMENSIONS: [bad]})


def test_a_day_at_the_row_cap_says_so():
    rows = [row([D, f"https://t.com/{n}", f"q{n}", "MOBILE"], 0, 1, 1.0) for n in range(DAILY_ROW_CAP)]
    data, _ = fetch({QUERY_DIMENSIONS: rows})
    assert data.query_rows_capped is True


def test_a_day_under_the_cap_does_not():
    data, _ = fetch({QUERY_DIMENSIONS: [row([D, "https://t.com/a", "q", "MOBILE"], 0, 1, 1.0)]})
    assert data.query_rows_capped is False


def test_a_row_sent_twice_by_overlapping_pages_is_counted_once():
    repeated = row([D, "https://tennisino.com/a", "MOBILE"], 3, 30, 2.0)
    data, _ = fetch({PAGE_DIMENSIONS: [repeated, dict(repeated)]})
    [page] = data.pages
    assert (page.clicks, page.impressions) == (3, 30), "not doubled"


def test_the_same_row_twice_with_different_numbers_is_refused():
    with pytest.raises(FetchError, match="twice"):
        fetch({TOTALS_DIMENSIONS: [row([D, "MOBILE"], 1, 10, 1.0), row([D, "MOBILE"], 2, 10, 1.0)]})


def test_different_raw_rows_that_normalise_to_one_are_still_summed():
    data, _ = fetch({PAGE_DIMENSIONS: [
        row([D, "https://tennisino.com/a", "MOBILE"], 1, 10, 1.0),
        row([D, "https://Tennisino.com/a", "MOBILE"], 2, 20, 1.0),
    ]})
    [page] = data.pages
    assert (page.clicks, page.impressions) == (3, 30)
