"""Live SERP sources: Serper and DataForSEO, their cache and budget, and their wiring."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.keyword_intel.cache import RequestBudget
from app.normalization.url import canonicalize_url
from app.persistence.memory import InMemoryRepository
from app.runtime.config import RuntimeConfig
from app.runtime.serp import build_serp_provider
from app.serp.cache import CachedSERPProvider
from app.serp.live import (
    DataForSEOSERPProvider,
    HTTPAnswer,
    SERPAuthError,
    SERPQuotaError,
    SERPRateLimitedError,
    SERPUnavailableError,
    SerperSERPProvider,
)
from app.serp.provider import SERPBudgetSpentError

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
QUERY = "خرید کفش مردانه"


class Recorder:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body))
        text = self.body if isinstance(self.body, str) else json.dumps(self.body, ensure_ascii=False)
        return HTTPAnswer(self.status, text)


SERPER_ANSWER = {
    "organic": [
        {"title": "کفش مردانه | دیجی‌کالا", "link": "https://www.digikala.com/a", "snippet": "خرید", "position": 1},
        {"title": "no link"},
        {"title": "کفش ملی", "link": "https://melli.ir/", "position": 2},
    ]
}


def serper(recorder):
    return SerperSERPProvider(api_key="secret-key", transport=recorder, clock=lambda: NOW)


# ---- Serper --------------------------------------------------------------------


def test_serper_asks_as_a_searcher_in_iran_and_reads_the_organic_results():
    recorder = Recorder(body=SERPER_ANSWER)

    snapshot = serper(recorder).search(query=QUERY)

    url, headers, body = recorder.calls[0]
    assert url == "https://google.serper.dev/search"
    assert headers["X-API-KEY"] == "secret-key"
    assert body == {"q": QUERY, "gl": "ir", "hl": "fa", "num": 10}
    assert [(r.position, r.url) for r in snapshot.results] == [
        (1, "https://www.digikala.com/a"), (2, "https://melli.ir/"),
    ], "a row without a link is not a result"
    assert snapshot.results[0].snippet == "خرید" and snapshot.results[1].snippet is None
    assert snapshot.provider == "serper" and snapshot.fetched_at == NOW
    assert snapshot.response_id == serper(Recorder(body=SERPER_ANSWER)).search(query=QUERY).response_id


def test_result_urls_are_canonical_so_the_customers_own_page_is_recognised():
    answer = {"organic": [
        {"title": "ours", "link": "http://Example.com/page?utm_source=x", "position": 1},
        {"title": "not a page", "link": "mailto:someone@example.com", "position": 2},
    ]}
    snapshot = serper(Recorder(body=answer)).search(query=QUERY)
    assert [result.url for result in snapshot.results] == [canonicalize_url("https://example.com/page")]


@pytest.mark.parametrize(
    "status,error", [(401, SERPAuthError), (403, SERPAuthError), (429, SERPRateLimitedError),
                     (402, SERPQuotaError), (500, SERPUnavailableError)],
)
def test_serper_errors_say_what_went_wrong(status, error):
    with pytest.raises(error):
        serper(Recorder(status=status, body={"message": "x"})).search(query=QUERY)


def test_serper_garbage_is_not_an_empty_result():
    with pytest.raises(SERPUnavailableError):
        serper(Recorder(body="<html>")).search(query=QUERY)


def test_the_key_never_shows_in_a_repr():
    assert "secret-key" not in repr(serper(Recorder()))
    assert "pass" not in repr(DataForSEOSERPProvider(credentials="me:pass", transport=Recorder()))


# ---- DataForSEO ----------------------------------------------------------------


def dataforseo_answer(items, task_code=20000, code=20000):
    return {
        "status_code": code, "status_message": "Ok.",
        "tasks": [{"status_code": task_code, "status_message": "Ok.", "result": [{"items": items}]}],
    }


def test_dataforseo_asks_for_iran_in_persian_and_keeps_organic_results_only():
    recorder = Recorder(body=dataforseo_answer([
        {"type": "paid", "rank_group": 1, "url": "https://ad.example/", "title": "Ad"},
        {"type": "organic", "rank_group": 1, "url": "https://www.digikala.com/a", "title": "دیجی‌کالا",
         "description": "خرید"},
        {"type": "organic", "rank_group": 2, "url": "https://melli.ir/", "title": "کفش ملی"},
    ]))
    provider = DataForSEOSERPProvider(credentials="me:pass", transport=recorder, clock=lambda: NOW)

    snapshot = provider.search(query=QUERY)

    url, headers, body = recorder.calls[0]
    assert url.endswith("/v3/serp/google/organic/live/regular")
    assert headers["Authorization"].startswith("Basic ")
    assert body == [{"keyword": QUERY, "location_code": 2364, "language_code": "fa", "depth": 10}]
    assert [(r.position, r.url, r.snippet) for r in snapshot.results] == [
        (1, "https://www.digikala.com/a", "خرید"), (2, "https://melli.ir/", None),
    ]


@pytest.mark.parametrize(
    "answer,error",
    [
        (dataforseo_answer([], code=40100), SERPAuthError),
        (dataforseo_answer([], task_code=40200), SERPQuotaError),
        (dataforseo_answer([], code=40202), SERPRateLimitedError),
        (dataforseo_answer([], task_code=40501), SERPUnavailableError),
    ],
)
def test_dataforseo_reads_its_own_status_codes_under_http_200(answer, error):
    with pytest.raises(error):
        DataForSEOSERPProvider(credentials="me:pass", transport=Recorder(body=answer)).search(query=QUERY)


@pytest.mark.parametrize("credentials", ["no-colon", ":pass", "me:"])
def test_dataforseo_credentials_are_login_and_password(credentials):
    with pytest.raises(ValueError):
        DataForSEOSERPProvider(credentials=credentials, transport=Recorder())


def test_dataforseo_refuses_a_country_it_has_no_location_for():
    with pytest.raises(ValueError):
        DataForSEOSERPProvider(credentials="me:pass", country="zz", transport=Recorder())


# ---- cache and budget ----------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def cached(recorder, *, limit=10, clock=None):
    clock = clock or Clock()
    repository = InMemoryRepository()
    live = SerperSERPProvider(api_key="k", transport=recorder, clock=clock)
    budget = RequestBudget(repository, scope="serp:serper", daily_limit=limit, clock=clock)
    return CachedSERPProvider(live, repository, budget, scope="ir-fa", clock=clock), clock


def test_a_query_is_asked_once_a_week():
    recorder = Recorder(body=SERPER_ANSWER)
    provider, clock = cached(recorder)

    first = provider.search(query=QUERY)
    clock.now += timedelta(days=6)
    assert provider.search(query=QUERY) == first
    assert len(recorder.calls) == 1

    clock.now += timedelta(days=2)
    provider.search(query=QUERY)
    assert len(recorder.calls) == 2


def test_spelling_variants_of_one_query_share_the_cache():
    recorder = Recorder(body=SERPER_ANSWER)
    provider, _ = cached(recorder)
    provider.search(query="كفش مردانه")  # Arabic kaf
    provider.search(query="کفش  مردانه")
    assert len(recorder.calls) == 1


def test_out_of_budget_an_older_answer_beats_none():
    recorder = Recorder(body=SERPER_ANSWER)
    provider, clock = cached(recorder, limit=1)
    first = provider.search(query=QUERY)
    clock.now += timedelta(days=8)  # stale, and today's one request is spent below
    provider.search(query="something else")

    again = provider.search(query=QUERY)

    assert again.fetched_at == first.fetched_at, "the older answer, with its own age"
    with pytest.raises(SERPBudgetSpentError):
        provider.search(query="never asked")


def test_an_answer_older_than_a_month_is_not_passed_off_as_current():
    recorder = Recorder(body=SERPER_ANSWER)
    provider, clock = cached(recorder, limit=1)
    provider.search(query=QUERY)
    clock.now += timedelta(days=31)
    recorder.status = 402

    with pytest.raises(SERPQuotaError):
        provider.search(query=QUERY)


def test_a_provider_out_of_credits_spends_the_day_and_falls_back():
    recorder = Recorder(body=SERPER_ANSWER)
    provider, clock = cached(recorder, limit=10)
    provider.search(query=QUERY)
    clock.now += timedelta(days=8)
    recorder.status = 402

    assert provider.search(query=QUERY).fetched_at == NOW
    recorder.status = 200
    with pytest.raises(SERPBudgetSpentError):
        provider.search(query="another")  # the day is marked spent; nothing more is sent
    assert len(recorder.calls) == 2


def test_a_rate_limit_falls_back_without_spending_the_day():
    recorder = Recorder(body=SERPER_ANSWER)
    provider, clock = cached(recorder, limit=10)
    provider.search(query=QUERY)
    clock.now += timedelta(days=8)
    recorder.status = 429

    assert provider.search(query=QUERY).fetched_at == NOW
    recorder.status = 200
    provider.search(query="another")  # the next request goes out as usual
    assert len(recorder.calls) == 3


# ---- wiring ----------------------------------------------------------------


class Resolver:
    def __init__(self):
        self.asked = []

    def resolve(self, reference):
        self.asked.append(reference.key)
        return "me:pass"


def test_a_live_mode_needs_the_name_of_its_key():
    with pytest.raises(ValidationError):
        RuntimeConfig(api_key="k", database_dsn="d", serp_mode="serper")


@pytest.mark.parametrize("mode", ["serper", "dataforseo"])
def test_live_modes_are_built_cached_with_the_named_secret(mode):
    config = RuntimeConfig(api_key="k", database_dsn="d", serp_mode=mode, serp_api_key_ref="SERP_API_KEY")
    resolver = Resolver()

    provider = build_serp_provider(config, repository=InMemoryRepository(), secret_resolver=resolver)

    assert isinstance(provider, CachedSERPProvider)
    assert provider.provider_id == mode
    assert resolver.asked == ["SERP_API_KEY"]


def test_serp_settings_come_from_the_environment(monkeypatch):
    for name, value in {
        "SEO_AGENT_API_KEY": "k", "SEO_AGENT_DATABASE_DSN": "d", "SEO_AGENT_SERP_MODE": "serper",
        "SEO_AGENT_SERP_API_KEY_REF": "SERP_API_KEY", "SEO_AGENT_SERP_COUNTRY": "IR",
        "SEO_AGENT_SERP_DAILY_BUDGET": "50",
    }.items():
        monkeypatch.setenv(name, value)
    config = RuntimeConfig.from_environment()
    assert (config.serp_mode, config.serp_api_key_ref, config.serp_country, config.serp_daily_budget) == (
        "serper", "SERP_API_KEY", "ir", 50,
    )
