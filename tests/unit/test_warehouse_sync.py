"""DaySync: a failure is recorded and raised; a day not final yet is neither."""
from datetime import date

import pytest

from app.warehouse.fetch import DayNotFinalError, FetchError, QueryAnswer
from app.warehouse.models import DaySyncResult
from app.warehouse.sync import DaySync


DAY = date(2026, 9, 30)


class FakeStore:
    def __init__(self):
        self.stored = []
        self.failures = []

    def replace_day(self, data):
        self.stored.append(data)

    def record_failure(self, **kwargs):
        self.failures.append(kwargs)


def answering(rows_by_dims=None, *, incomplete=None):
    def query(dimensions, day, data_state):
        return QueryAnswer(
            rows=(rows_by_dims or {}).get(dimensions, []),
            first_incomplete_date=incomplete if data_state == "all" else None,
        )
    return query


def sync(store, query, final_through=DAY):
    return DaySync(store).sync_day(
        site_id="s1", property_url="sc-domain:t.com", day=DAY, final_through=final_through, query=query
    )


def test_a_good_day_is_stored_and_described():
    store = FakeStore()
    result = sync(store, answering())
    assert isinstance(result, DaySyncResult)
    assert len(store.stored) == 1 and store.failures == []


def test_a_failure_is_recorded_then_raised():
    store = FakeStore()

    def broken(dimensions, day, data_state):
        raise FetchError("bad row")

    with pytest.raises(FetchError):
        sync(store, broken)
    assert store.stored == []
    [failure] = store.failures
    assert failure["day"] == DAY and "FetchError: bad row" in failure["error"]


def test_a_day_not_final_yet_is_neither_stored_nor_recorded_as_failed():
    store = FakeStore()
    with pytest.raises(DayNotFinalError):
        sync(store, answering(incomplete=DAY))
    assert store.stored == [] and store.failures == []


def test_a_day_past_the_settled_one_is_neither_stored_nor_recorded():
    store = FakeStore()
    with pytest.raises(DayNotFinalError):
        sync(store, answering(), final_through=date(2026, 9, 29))
    assert store.stored == [] and store.failures == []
