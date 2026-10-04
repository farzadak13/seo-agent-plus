"""SEO Signal search volume behind a provider-neutral port, with a cache and a daily budget."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.keyword_intel.cache import CachedSearchVolume, RequestBudget
from app.keyword_intel.contracts import (
    BudgetExhaustedError,
    KeywordProviderError,
    ProviderAuthError,
    ProviderPlanError,
    ProviderQuotaError,
    ProviderUnavailableError,
)
from app.keyword_intel.seosignal import RawResponse, SeoSignalClient, SeoSignalSearchVolume, parse_body
from app.models.keyword_intel import Competition
from app.persistence.memory import InMemoryRepository


NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)  # 12:30 in Tehran
BOM = "﻿﻿"  # what the live service puts in front of its JSON


def answer(status, body):
    return RawResponse(status, BOM + json.dumps(body, ensure_ascii=False))


class FakeSeoSignal:
    """Answers like the live API, byte-order marks included, and counts calls."""

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
            return answer(401, {"error": {"code": "INVALID_API_KEY", "message": "..."}})
        if endpoint == "keyword-research-bulk":
            if not body["keywords"]:
                return answer(400, {"error": {"code": "INVALID_PARAMS", "message": "..."}})
            rows = [
                {"Word": word, "SearchVolume": volume, "Competition": level}
                for word, (volume, level) in self.volumes.items()
                if word in body["keywords"]
            ]
            return answer(200, {"ok": True, "data": {"keywordList": rows, "All": len(body["keywords"])}})
        return RawResponse(404, "")


def client(fake, key="user@example.com:token"):
    return SeoSignalClient(api_key=key, transport=fake)


def cached_volumes(fake, *, limit=45, clock=lambda: NOW, repository=None):
    repository = repository or InMemoryRepository()
    budget = RequestBudget(repository, scope="seosignal", daily_limit=limit, clock=clock)
    provider = SeoSignalSearchVolume(client(fake), clock=clock)
    return CachedSearchVolume(provider, repository, budget, clock=clock), budget, repository


# ---- the adapter -----------------------------------------------------------


def test_the_byte_order_marks_in_front_of_the_json_are_not_mistaken_for_no_answer():
    # The bug the first live check found: every answer read as unparseable.
    assert parse_body(BOM + '{"ok": true, "data": 1}') == {"ok": True, "data": 1}


def test_volumes_come_back_in_our_terms_one_per_keyword_asked():
    fake = FakeSeoSignal()
    volumes = SeoSignalSearchVolume(client(fake), clock=lambda: NOW).search_volumes(
        ["کفش مردانه", "کلمه بی‌داده"]
    )
    assert [(v.keyword, v.search_volume, v.competition, v.confirmed) for v in volumes] == [
        ("کفش مردانه", 12000, Competition.MEDIUM, True),
        ("کلمه بی‌داده", None, Competition.UNKNOWN, True),
    ]
    assert fake.calls[0][0] == "keyword-research-bulk"


def test_arabic_and_persian_letters_are_the_same_keyword():
    fake = FakeSeoSignal()
    [volume] = SeoSignalSearchVolume(client(fake), clock=lambda: NOW).search_volumes(["كفش مردانه"])
    assert volume.search_volume == 12000
    assert fake.calls[0][1]["keywords"] == ["کفش مردانه"], "sent with Persian letters"


@pytest.mark.parametrize(
    "response, error",
    [
        (answer(401, {"error": {"code": "INVALID_API_KEY"}}), ProviderAuthError),
        (answer(403, {"error": {"code": "ACCOUNT_DISABLED"}}), ProviderAuthError),
        (answer(403, {"error": {"code": "PLAN_NOT_ALLOWED"}}), ProviderPlanError),
        (answer(429, {"error": {"code": "RATE_LIMIT_EXCEEDED", "limit": 50, "used": 50}}), ProviderQuotaError),
        (answer(502, {"error": {"code": "SOMETHING"}}), ProviderUnavailableError),
    ],
)
def test_provider_errors_become_ours(response, error):
    fake = FakeSeoSignal()
    fake.fail_with = response
    with pytest.raises(error):
        SeoSignalSearchVolume(client(fake)).search_volumes(["x"])


def test_an_answer_of_unexpected_shape_is_described_to_the_operator_only():
    fake = FakeSeoSignal()
    fake.fail_with = RawResponse(200, "<html>maintenance</html>")
    with pytest.raises(KeywordProviderError) as raised:
        SeoSignalSearchVolume(client(fake)).search_volumes(["x"])
    assert "maintenance" not in str(raised.value), "not in what a customer sees"
    assert "maintenance" in raised.value.detail, "but kept for the operator"


@pytest.mark.parametrize(
    "status, error",
    [(429, ProviderQuotaError), (401, ProviderAuthError), (502, ProviderUnavailableError)],
)
def test_a_gateway_page_still_fails_by_its_status(status, error):
    fake = FakeSeoSignal()
    fake.fail_with = RawResponse(status, "<html>gateway</html>")
    with pytest.raises(error):
        SeoSignalSearchVolume(client(fake)).search_volumes(["x"])


def test_a_gateway_429_still_marks_the_day_spent():
    fake = FakeSeoSignal()
    fake.fail_with = RawResponse(429, "<html>Too Many Requests</html>")
    service, budget, _ = cached_volumes(fake)
    service.lookup(["کفش مردانه"])
    assert budget.remaining() == 0


def test_the_key_check_asks_no_keyword():
    fake = FakeSeoSignal()
    SeoSignalSearchVolume(client(fake)).check_key()
    assert fake.calls[0][1] == {"keywords": []}


def test_the_key_check_names_a_refused_key():
    with pytest.raises(ProviderAuthError):
        SeoSignalSearchVolume(client(FakeSeoSignal(), key="wrong")).check_key()


def test_nothing_found_for_the_whole_request_is_an_unconfirmed_answer():
    fake = FakeSeoSignal()
    fake.fail_with = answer(422, {"error": {"code": "REQUEST_FAILED"}})
    [volume] = SeoSignalSearchVolume(client(fake), clock=lambda: NOW).search_volumes(["x"])
    assert volume.search_volume is None and volume.confirmed is False


def test_the_key_never_appears_in_repr():
    assert "token" not in repr(client(FakeSeoSignal()))


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


def test_an_unconfirmed_nothing_is_kept_a_day_not_a_month():
    fake = FakeSeoSignal()
    fake.fail_with = answer(422, {"error": {"code": "REQUEST_FAILED"}})
    repository = InMemoryRepository()
    cached_volumes(fake, repository=repository)[0].lookup(["کفش مردانه"])
    fake.fail_with = None

    same_day = NOW + timedelta(hours=20)
    assert cached_volumes(fake, repository=repository, clock=lambda: same_day)[0].lookup(
        ["کفش مردانه"]
    ).requests_made == 0

    next_day = NOW + timedelta(hours=25)
    refreshed = cached_volumes(fake, repository=repository, clock=lambda: next_day)[0].lookup(["کفش مردانه"])
    assert refreshed.requests_made == 1
    assert refreshed.volumes[0].search_volume == 12000


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
    fake.fail_with = answer(429, {"error": {"code": "RATE_LIMIT_EXCEEDED"}})
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


def test_no_raw_byte_order_mark_hides_in_the_adapter_source():
    # Written as an escape, so an editor cannot silently drop it.
    import app.keyword_intel.seosignal as module

    source = open(module.__file__, encoding="utf-8").read()
    assert chr(0xFEFF) not in source
