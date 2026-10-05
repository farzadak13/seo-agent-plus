"""The dashboard's read endpoints: who may read, what is checked, and what is added."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.sites import GSCConnectionConfig, SecretProvider, SecretRef, Site
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.warehouse.models import MetricValues, PerformanceReport, TableReport, TableRow


NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)  # window ends 2026-10-02
OWN_GOOGLE = GSCConnectionConfig(
    property_url="sc-domain:tennisino.com",
    credential_ref=SecretRef(provider=SecretProvider.DATABASE, key="tenant:p1/google_refresh_token"),
    auth_mode="oauth_refresh_token",
)


class FakeReports:
    def __init__(self):
        self.calls = []

    def performance(self, **kwargs):
        self.calls.append(("performance", kwargs))
        return PerformanceReport(
            site_id=kwargs["site_id"], start=kwargs["start"], end=kwargs["end"],
            interval=kwargs["interval"], totals=MetricValues(clicks=0, impressions=0),
            days_covered=0, days_in_range=(kwargs["end"] - kwargs["start"]).days + 1, series=[],
        )

    def _table(self, kind, kwargs):
        self.calls.append((kind, kwargs))
        return TableReport(
            site_id=kwargs["site_id"], start=kwargs["start"], end=kwargs["end"],
            sort=kwargs["sort"], order=kwargs["order"],
            rows=[TableRow(key="راکت تنیس", current=MetricValues(clicks=1, impressions=10)),
                  TableRow(key="توپ تنیس", current=MetricValues(clicks=1, impressions=10))],
        )

    def pages(self, **kwargs):
        return self._table("pages", kwargs)

    def queries(self, **kwargs):
        return self._table("queries", kwargs)


class FakeKeywords:
    def __init__(self):
        self.asked = []

    def cached_volumes(self, keywords):
        from app.models.keyword_intel import KeywordVolume

        self.asked.append(list(keywords))
        return {"راکت تنیس": KeywordVolume(keyword="راکت تنیس", search_volume=900,
                                           provider_id="seosignal", fetched_at=NOW)}


def client(site=None, *, reports="default", keywords=None):
    repository = InMemoryRepository()
    sites = SiteStore(repository)
    sites.create(site or Site(site_id="s1", principal_id="p1", name="T",
                              base_url="https://tennisino.com/", gsc=OWN_GOOGLE))
    reports = FakeReports() if reports == "default" else reports
    app = create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator("k", principal_id="p1"),
        site_store=sites, warehouse_reports=reports, keywords=keywords, clock=lambda: NOW,
    ))
    test_client = TestClient(app)
    test_client.headers["Authorization"] = "Bearer k"
    return test_client, reports


def test_the_default_range_is_the_last_28_settled_days():
    api, reports = client()
    assert api.get("/v1/sites/s1/performance").status_code == 200
    _, kwargs = reports.calls[0]
    assert (kwargs["start"], kwargs["end"]) == (date(2026, 9, 5), date(2026, 10, 2))
    assert kwargs["property_url"] == "sc-domain:tennisino.com", "the site's current property"


@pytest.mark.parametrize(
    "query",
    [
        "interval=hour",
        "start=2026-09-10&end=2026-09-01",
        "start=2024-01-01&end=2026-10-01",
        "device=SMARTWATCH",
    ],
)
def test_bad_chart_parameters_are_refused(query):
    api, _ = client()
    assert api.get(f"/v1/sites/s1/performance?{query}").status_code == 422


@pytest.mark.parametrize(
    "query", ["sort=name", "order=sideways", "compare=lastyear", "sort=clicks;DROP TABLE gsc_pages"]
)
def test_bad_table_parameters_never_reach_the_sql(query):
    api, reports = client()
    assert api.get(f"/v1/sites/s1/performance/pages?{query}").status_code == 422
    assert reports.calls == []


def test_compare_previous_is_passed_on():
    api, reports = client()
    api.get("/v1/sites/s1/performance/pages?compare=previous&sort=position&order=asc")
    _, kwargs = reports.calls[0]
    assert kwargs["compare"] is True and kwargs["sort"] == "position"


def test_a_page_filter_is_canonicalised_before_it_is_looked_up():
    api, reports = client()
    api.get("/v1/sites/s1/performance/queries", params={"page": "https://Tennisino.com/rackets?utm_source=x"})
    _, kwargs = reports.calls[0]
    assert kwargs["page_url"] == "https://tennisino.com/rackets"


def test_queries_carry_search_volume_from_the_cache_only():
    keywords = FakeKeywords()
    api, _ = client(keywords=keywords)
    rows = api.get("/v1/sites/s1/performance/queries").json()["rows"]
    assert [(r["key"], r["search_volume"]) for r in rows] == [("راکت تنیس", 900), ("توپ تنیس", None)]
    assert keywords.asked == [["راکت تنیس", "توپ تنیس"]]


def test_the_owner_only():
    api, _ = client(Site(site_id="s1", principal_id="p2", name="T",
                         base_url="https://tennisino.com/", gsc=OWN_GOOGLE))
    assert api.get("/v1/sites/s1/performance").status_code == 404


def test_a_site_not_connected_or_not_eligible_is_refused():
    api, _ = client(Site(site_id="s1", principal_id="p1", name="T", base_url="https://tennisino.com/"))
    assert api.get("/v1/sites/s1/performance").status_code == 409
    shared = GSCConnectionConfig(property_url="sc-domain:tennisino.com",
                                 credential_ref=SecretRef(key="GSC_KEY"), auth_mode="service_account")
    api, _ = client(Site(site_id="s1", principal_id="p1", name="T",
                         base_url="https://tennisino.com/", gsc=shared))
    assert api.get("/v1/sites/s1/performance/pages").status_code == 409


def test_off_when_the_warehouse_is_off():
    api, _ = client(reports=None)
    assert api.get("/v1/sites/s1/performance").status_code == 503


def test_the_cache_peek_never_asks_the_provider():
    from app.keyword_intel.cache import CachedSearchVolume, RequestBudget
    from app.keyword_intel.seosignal import SeoSignalSearchVolume
    from tests.unit.test_keyword_intel import FakeSeoSignal, client as seosignal_client

    repository = InMemoryRepository()
    fake = FakeSeoSignal()
    budget = RequestBudget(repository, scope="s", daily_limit=10, clock=lambda: NOW)
    service = CachedSearchVolume(SeoSignalSearchVolume(seosignal_client(fake), clock=lambda: NOW),
                                 repository, budget, clock=lambda: NOW)
    service.lookup(["کفش مردانه"])
    calls_before = len(fake.calls)

    found = service.peek(["کفش مردانه", "کلمه‌ای که پرسیده نشده"])

    assert set(found) == {"کفش مردانه"}
    assert len(fake.calls) == calls_before and budget.used() == 1


def test_the_allowed_values_are_published_in_the_api_description():
    api, _ = client()
    schema = api.get("/openapi.json").json()
    parameters = {p["name"]: p for p in schema["paths"]["/v1/sites/{site_id}/performance/pages"]["get"]["parameters"]}
    sort_schema = parameters["sort"]["schema"]
    ref = sort_schema.get("$ref") or sort_schema.get("allOf", [{}])[0].get("$ref")
    enum = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]["enum"]
    assert set(enum) == {"clicks", "impressions", "ctr", "position"}



def test_a_failing_keyword_cache_leaves_the_table_without_volumes():
    class BrokenKeywords:
        def cached_volumes(self, keywords):
            raise RuntimeError("database briefly away")

    api, _ = client(keywords=BrokenKeywords())
    response = api.get("/v1/sites/s1/performance/queries")
    assert response.status_code == 200
    assert [row["search_volume"] for row in response.json()["rows"]] == [None, None]


def test_cached_volumes_reads_the_cache_and_spends_nothing():
    from app.keyword_intel.seosignal import SeoSignalSearchVolume
    from app.runtime.keywords import KeywordIntel
    from tests.unit.test_keyword_intel import FakeSeoSignal, client as seosignal_client

    repository = InMemoryRepository()
    fake = FakeSeoSignal()
    intel = KeywordIntel(
        volumes=SeoSignalSearchVolume(seosignal_client(fake), clock=lambda: NOW),
        repository=repository, daily_budget=40, tenant_daily_budget=10, clock=lambda: NOW,
    )
    intel.volumes_for("p1").lookup(["کفش مردانه"])
    calls = len(fake.calls)

    found = intel.cached_volumes(["كفش مردانه", "کفش ورزشی"])  # Arabic kaf in the first

    assert {k: v.search_volume for k, v in found.items()} == {"کفش مردانه": 12000}
    assert len(fake.calls) == calls


def test_a_peek_reads_on_one_connection():
    from contextlib import contextmanager

    from app.keyword_intel.cache import CachedSearchVolume, RequestBudget
    from app.keyword_intel.seosignal import SeoSignalSearchVolume
    from tests.unit.test_keyword_intel import FakeSeoSignal, client as seosignal_client

    class CountingRepository(InMemoryRepository):
        transactions = 0
        reads_outside = 0
        inside = False

        @contextmanager
        def transaction(self):
            CountingRepository.transactions += 1
            CountingRepository.inside = True
            try:
                yield
            finally:
                CountingRepository.inside = False

        def get(self, **kwargs):
            if not CountingRepository.inside:
                CountingRepository.reads_outside += 1
            return super().get(**kwargs)

    repository = CountingRepository()
    service = CachedSearchVolume(
        SeoSignalSearchVolume(seosignal_client(FakeSeoSignal()), clock=lambda: NOW), repository,
        RequestBudget(repository, scope="s", daily_limit=10, clock=lambda: NOW), clock=lambda: NOW,
    )
    service.peek([f"کلمه {n}" for n in range(50)])
    assert CountingRepository.transactions == 1 and CountingRepository.reads_outside == 0
