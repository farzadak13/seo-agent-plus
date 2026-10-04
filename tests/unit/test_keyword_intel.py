"""SEO Signal behind provider-neutral ports, with a cache and a daily budget."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.keyword_intel.cache import CachedRankTracker, CachedSearchVolume, RequestBudget
from app.keyword_intel.contracts import (
    BudgetExhaustedError,
    ProviderAuthError,
    ProviderPlanError,
    ProviderQuotaError,
    ProviderUnavailableError,
)
from app.keyword_intel.seosignal import (
    SeoSignalClient,
    SeoSignalRankTracker,
    SeoSignalSearchVolume,
)
from app.models.keyword_intel import Competition, Device
from app.persistence.memory import InMemoryRepository


NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)  # 12:30 in Tehran


class FakeSeoSignal:
    """Answers like the documented API, and counts what it was asked."""

    def __init__(self):
        self.calls = []
        self.volumes = {"کفش مردانه": (12000, "معمولی"), "کفش ورزشی": (2400, "زیاد")}
        self.fail_with = None

    def __call__(self, url, headers, body, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, body, headers))
        if self.fail_with:
            return self.fail_with
        if headers.get("X-Api-Key") != "user@example.com:token":
            return 401, {"error": {"code": "INVALID_API_KEY", "message": "..."}}
        if endpoint == "keyword-research-bulk":
            rows = [
                {"Word": word, "SearchVolume": volume, "Competition": level}
                for word, (volume, level) in self.volumes.items()
                if word in body["keywords"]
            ]
            return 200, {"ok": True, "data": {"keywordList": rows, "All": len(body["keywords"])}}
        if endpoint == "ranktracker-projects":
            return 200, {"ok": True, "data": {"List": [
                {"Id": 37113, "Name": "تنیسینو", "MainDomain": "tennisino.com", "IsActive": True}
            ]}}
        if endpoint == "ranktracker-project-detail":
            return 200, {"ok": True, "data": {
                "name": "تنیسینو", "main_domain": "tennisino.com", "keywords": ["راکت تنیس"],
                "competitor_domains": ["rival.ir"], "location": "تهران", "country_code": "IR",
                "mobile_id": 87, "desktop_id": 88,
            }}
        if endpoint == "ranktracker-rank-history":
            return 200, {"ok": True, "data": {
                "domain": "tennisino.com", "device_id": body["device_id"],
                "keywords": [{
                    "keyword": "راکت تنیس", "search_volume": 900, "target_url": None,
                    "history": [{"date": "2026-09-29", "rank": 4}, {"date": "2026-09-28", "rank": 0}],
                }],
            }}
        return 404, None


def client(fake):
    return SeoSignalClient(api_key="user@example.com:token", transport=fake)


def cached_volumes(fake, *, limit=45, clock=lambda: NOW, repository=None):
    repository = repository or InMemoryRepository()
    budget = RequestBudget(repository, scope="seosignal", daily_limit=limit, clock=clock)
    provider = SeoSignalSearchVolume(client(fake), clock=clock)
    return CachedSearchVolume(provider, repository, budget, clock=clock), budget, repository


# ---- the adapter -----------------------------------------------------------


def test_volumes_come_back_in_our_terms_one_per_keyword_asked():
    fake = FakeSeoSignal()
    volumes = SeoSignalSearchVolume(client(fake), clock=lambda: NOW).search_volumes(
        ["کفش مردانه", "کلمه بی‌داده"]
    )
    assert [(v.keyword, v.search_volume, v.competition) for v in volumes] == [
        ("کفش مردانه", 12000, Competition.MEDIUM),
        ("کلمه بی‌داده", None, Competition.UNKNOWN),
    ]
    assert fake.calls[0][0] == "keyword-research-bulk"


def test_arabic_and_persian_letters_are_the_same_keyword():
    fake = FakeSeoSignal()
    [volume] = SeoSignalSearchVolume(client(fake), clock=lambda: NOW).search_volumes(["كفش مردانه"])
    assert volume.search_volume == 12000


@pytest.mark.parametrize(
    "answer, error",
    [
        ((401, {"error": {"code": "INVALID_API_KEY"}}), ProviderAuthError),
        ((403, {"error": {"code": "ACCOUNT_DISABLED"}}), ProviderAuthError),
        ((403, {"error": {"code": "PLAN_NOT_ALLOWED"}}), ProviderPlanError),
        ((429, {"error": {"code": "RATE_LIMIT_EXCEEDED", "limit": 50, "used": 50}}), ProviderQuotaError),
        ((502, None), ProviderUnavailableError),
    ],
)
def test_provider_errors_become_ours(answer, error):
    fake = FakeSeoSignal()
    fake.fail_with = answer
    with pytest.raises(error):
        SeoSignalSearchVolume(client(fake)).search_volumes(["x"])


def test_no_data_at_all_is_an_answer_not_an_error():
    fake = FakeSeoSignal()
    fake.fail_with = (422, {"error": {"code": "REQUEST_FAILED"}})
    [volume] = SeoSignalSearchVolume(client(fake), clock=lambda: NOW).search_volumes(["x"])
    assert volume.search_volume is None


def test_the_key_never_appears_in_repr():
    assert "token" not in repr(client(FakeSeoSignal()))


def test_rank_projects_and_history_in_our_terms():
    tracker = SeoSignalRankTracker(client(FakeSeoSignal()), clock=lambda: NOW)
    [summary] = tracker.projects()
    assert (summary.project_id, summary.domain) == ("37113", "tennisino.com")

    project = tracker.project("37113")
    assert project.devices == [Device.MOBILE, Device.DESKTOP]
    assert project.competitor_domains == ["rival.ir"]

    history = tracker.rank_history(
        project_id="37113", device=Device.DESKTOP, start=date(2026, 9, 28), end=date(2026, 9, 29)
    )
    [keyword] = history.keywords
    assert [(p.date.isoformat(), p.rank) for p in keyword.points] == [
        ("2026-09-28", None),  # 0 means not found that day
        ("2026-09-29", 4),
    ]


def test_rank_history_is_limited_to_the_providers_window():
    tracker = SeoSignalRankTracker(client(FakeSeoSignal()))
    with pytest.raises(ValueError):
        tracker.rank_history(
            project_id="37113", device=Device.MOBILE, start=date(2026, 1, 1), end=date(2026, 9, 1)
        )


# ---- cache and budget ------------------------------------------------------


def test_a_keyword_is_asked_about_once_a_month_not_once_a_page():
    fake = FakeSeoSignal()
    service, budget, _ = cached_volumes(fake)

    first = service.lookup(["کفش مردانه", "کفش ورزشی", "کفش مردانه"])
    second = service.lookup(["کفش ورزشی", "کفش مردانه"])

    assert first.requests_made == 1, "both keywords in one bulk request"
    assert second.requests_made == 0
    assert len(fake.calls) == 1
    assert budget.used() == 1
    assert [v.search_volume for v in second.volumes] == [2400, 12000]


def test_no_data_is_remembered_too():
    fake = FakeSeoSignal()
    service, _, _ = cached_volumes(fake)
    service.lookup(["کلمه بی‌داده"])
    again = service.lookup(["کلمه بی‌داده"])
    assert again.requests_made == 0 and again.volumes[0].search_volume is None


def test_only_the_missing_keywords_are_sent():
    fake = FakeSeoSignal()
    service, _, _ = cached_volumes(fake)
    service.lookup(["کفش مردانه"])
    service.lookup(["کفش مردانه", "کفش ورزشی"])
    assert fake.calls[1][1]["keywords"] == ["کفش ورزشی"]


def test_the_cache_survives_a_restart():
    fake = FakeSeoSignal()
    repository = InMemoryRepository()
    cached_volumes(fake, repository=repository)[0].lookup(["کفش مردانه"])
    restarted, _, _ = cached_volumes(fake, repository=repository)
    assert restarted.lookup(["کفش مردانه"]).requests_made == 0


def test_an_old_answer_is_refreshed_after_a_month():
    fake = FakeSeoSignal()
    repository = InMemoryRepository()
    cached_volumes(fake, repository=repository)[0].lookup(["کفش مردانه"])
    later = NOW + timedelta(days=31)
    service, _, _ = cached_volumes(fake, repository=repository, clock=lambda: later)
    assert service.lookup(["کفش مردانه"]).requests_made == 1


def test_the_budget_refuses_before_the_provider_has_to():
    fake = FakeSeoSignal()
    service, budget, _ = cached_volumes(fake, limit=1)
    service.lookup(["کفش مردانه"])

    result = service.lookup(["کفش ورزشی"])

    assert result.pending == ["کفش ورزشی"]
    assert result.volumes == []
    assert len(fake.calls) == 1, "nothing sent once the budget is spent"
    with pytest.raises(BudgetExhaustedError):
        budget.consume()


def test_out_of_budget_an_older_answer_is_better_than_none():
    fake = FakeSeoSignal()
    repository = InMemoryRepository()
    cached_volumes(fake, repository=repository)[0].lookup(["کفش مردانه"])
    later = NOW + timedelta(days=40)
    service, _, _ = cached_volumes(fake, repository=repository, clock=lambda: later, limit=0)

    result = service.lookup(["کفش مردانه"])

    assert result.pending == []
    assert result.volumes[0].search_volume == 12000
    assert result.volumes[0].fetched_at == NOW, "its age is visible"


def test_when_the_provider_says_the_day_is_spent_we_stop_asking():
    fake = FakeSeoSignal()
    fake.fail_with = (429, {"error": {"code": "RATE_LIMIT_EXCEEDED"}})
    service, budget, _ = cached_volumes(fake)

    result = service.lookup(["کفش مردانه"])

    assert result.pending == ["کفش مردانه"]
    assert budget.remaining() == 0
    service.lookup(["کفش ورزشی"])
    assert len(fake.calls) == 1


def test_the_budget_resets_at_midnight_tehran_time():
    repository = InMemoryRepository()
    before = datetime(2026, 10, 4, 20, 29, tzinfo=timezone.utc)  # 23:59 Tehran
    after = datetime(2026, 10, 4, 20, 31, tzinfo=timezone.utc)  # 00:01 Tehran, next day
    RequestBudget(repository, scope="s", daily_limit=1, clock=lambda: before).consume()
    with pytest.raises(BudgetExhaustedError):
        RequestBudget(repository, scope="s", daily_limit=1, clock=lambda: before).consume()
    RequestBudget(repository, scope="s", daily_limit=1, clock=lambda: after).consume()


def test_rank_history_is_cached_for_a_few_hours():
    fake = FakeSeoSignal()
    repository = InMemoryRepository()
    times = {"now": NOW}
    tracker = CachedRankTracker(
        SeoSignalRankTracker(client(fake), clock=lambda: times["now"]), repository,
        clock=lambda: times["now"],
    )
    args = dict(project_id="37113", device=Device.MOBILE, start=date(2026, 9, 28), end=date(2026, 9, 29))

    tracker.rank_history(**args)
    calls_after_first = len(fake.calls)
    tracker.rank_history(**args)
    assert len(fake.calls) == calls_after_first

    times["now"] = NOW + timedelta(hours=7)
    tracker.rank_history(**args)
    assert len(fake.calls) > calls_after_first
