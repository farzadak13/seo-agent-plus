"""Target words for a site with no Search Console data: suggested, kept, titled and ranked."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.action.store import ManagedActionStore
from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.api.target_routes import MAX_CONFIRMED
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.serp import SERPQuerySnapshot, SERPResultItem
from app.models.serp_decision import SERPDecisionStatus
from app.models.sites import Site, SiteAdapterConnection, SiteStatus
from app.models.target_keywords import KeywordStatus, SuggestionStatus
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.reasoning.keywords import KeywordSuggester, KeywordSuggestionError, PageForKeywords, parse_keyword_answer
from app.runs.store import RunStore
from app.scan.crawler import SiteCrawler, _identity
from app.scan.handler import SITE_SCAN_JOB_TYPE, build_site_scan_handler
from app.scan.store import SiteFileStore, SiteScanStore
from app.serp.decision import build_serp_decision
from app.serp.engine import build_serp_investigation
from app.serp.provider import SERPProviderError
from app.targets.handler import (
    TARGET_SUGGEST_JOB_TYPE,
    TARGET_TITLE_JOB_TYPE,
    RankTracker,
    build_target_suggestion_handler,
    build_target_title_handler,
    target_strategy,
)
from app.normalization.url import canonicalize_url
from app.targets.store import TargetKeywordStore
from app.title.workflow import TitleProposalStore, TitleRecommendationWorkflow
from tests.unit.test_site_scan import BASE, FakeSite, html
from app.models.reasoning import TitleReasoningCandidate
from tests.unit.test_stage36_deterministic_ids import FakeAdapter, FakeReasoner, make_router

API_KEY = "target-secret"
PAGES = [
    PageForKeywords(url=BASE, title="فروشگاه کفش", h1="کفش"),
    PageForKeywords(url=f"{BASE}a", title="کفش ورزشی", h1="کفش ورزشی"),
]


def answer(*items):
    return json.dumps({"keywords": [dict(zip(("keyword", "url", "intent", "reason"), item)) for item in items]})


class FakeModel:
    provider_id = "fake-llm"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def complete(self, *, messages):
        self.calls += 1
        reply = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


class Reasoner(FakeReasoner):
    def generate_title_candidates(self, reasoning_input):
        return [TitleReasoningCandidate(
            candidate_id="candidate-1", title="خرید کفش ورزشی اصل در مدل‌های گوناگون",
            rationale="بر پایهٔ نتایج گوگل", confidence_score=0.9,
        )]


class SERP:
    """Three competitors; the customer's page is in the results only when told to be."""

    provider_id = "fake-serp"

    def __init__(self, own=None):
        self.own = own
        self.asked = []
        self.fail = False
        # When Google gave the answer; a cache hands back old ones with their own date.
        self.fetched_at = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def search(self, *, query):
        if self.fail:
            raise SERPProviderError("out of budget")
        self.asked.append(query)
        results = [
            SERPResultItem(position=1, url="https://a.example/1", title="خرید کفش ورزشی"),
            SERPResultItem(position=2, url="https://b.example/2", title="قیمت کفش ورزشی"),
            SERPResultItem(position=3, url="https://c.example/3", title="بهترین کفش ورزشی"),
        ]
        if self.own:
            results.append(SERPResultItem(position=self.own[0], url=self.own[1], title="کفش"))
        return SERPQuerySnapshot(
            query=query, provider=self.provider_id, response_id=f"serp:{query}",
            fetched_at=self.fetched_at, results=results,
        )


class World:
    def __init__(self, *, model=None, adapter_type="manual", serp=None):
        self.repository = InMemoryRepository()
        self.sites = SiteStore(self.repository)
        self.scans = SiteScanStore(self.repository)
        self.targets = TargetKeywordStore(self.repository)
        self.runs = RunStore(self.repository)
        self.actions = ManagedActionStore(self.repository)
        self.now = datetime.now(timezone.utc)
        self.model = model or FakeModel(answer(("کفش ورزشی", f"{BASE}a", "transactional", "صفحهٔ کفش ورزشی")))
        self.serp = serp or SERP()
        connection = SiteAdapterConnection(adapter_type=adapter_type) if adapter_type else None
        self.sites.create(Site(site_id="site-1", principal_id="principal-1", name="n", base_url=BASE, site_adapter=connection))
        site = FakeSite({"/": html(links=["/a"]), "/a": html("صفحهٔ کفش ورزشی با عنوانی کاملاً کافی")})
        handlers = JobHandlerRegistry()
        handlers.register(SITE_SCAN_JOB_TYPE, build_site_scan_handler(
            site_store=self.sites, scan_store=self.scans, file_store=SiteFileStore(self.repository),
            crawler_factory=lambda: SiteCrawler(fetcher=site, delay_seconds=0, sleep=lambda _: None),
        ))
        handlers.register(TARGET_SUGGEST_JOB_TYPE, build_target_suggestion_handler(
            site_store=self.sites, scan_store=self.scans, target_store=self.targets,
            suggester=KeywordSuggester(transport=self.model), clock=lambda: self.now,
        ))
        workflow = TitleRecommendationWorkflow(
            adapter_resolver=lambda site: FakeAdapter(),
            serp_provider=self.serp,
            reasoning_router=make_router(Reasoner()),
            proposal_store=TitleProposalStore(self.repository),
        )
        self.workflow = workflow
        handlers.register(TARGET_TITLE_JOB_TYPE, build_target_title_handler(
            site_store=self.sites, run_store=self.runs, action_store=self.actions, title_workflow=workflow,
            proposal_store=TitleProposalStore(self.repository),
        ))
        run_ids = iter(f"run-{number}" for number in range(1, 100))
        app = create_app(APIDependencies(
            scheduler=JobScheduler(store=JobStore(self.repository), handlers=handlers),
            authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
            site_store=self.sites, run_store=self.runs, action_store=self.actions,
            scan_store=self.scans, file_store=SiteFileStore(self.repository), target_store=self.targets,
            run_id_factory=lambda: next(run_ids),
            clock=lambda: self.now,
        ))
        self.client = TestClient(app)
        self.client.headers["Authorization"] = f"Bearer {API_KEY}"
        self.scheduler = app.state.dependencies.scheduler

    def run_jobs(self):
        while self.scheduler.run_once() is not None:
            pass

    def scan(self):
        assert self.client.post("/v1/sites/site-1/scans").status_code == 202
        self.run_jobs()

    def call(self, method, path="", expect=200, run=True, site="site-1", **body):
        response = self.client.request(method, f"/v1/sites/{site}/target-keywords{path}", json=body or None)
        assert response.status_code == expect, response.text
        if run:
            self.run_jobs()
        return response.json()

    def suggest(self):
        self.call("POST", "/suggest", expect=202)
        return self.targets.find("site-1")

    def tracker(self, **extra):
        return RankTracker(target_store=self.targets, site_store=self.sites, serp_provider=self.serp, clock=lambda: self.now, **extra)


# The model's answer is checked before anything uses it.


def test_an_answer_keeps_only_words_for_pages_it_was_shown():
    raw = answer(
        ("کفش ورزشی", f"{BASE}a", "transactional", "x"),
        ("کفش  ورزشی ", f"{BASE}a", "transactional", "the same word again"),
        ("کفش زنانه", "https://other.example/", "commercial", "a page that is not the site's"),
        ("کفش اصل", f"{BASE}missing", "commercial", "a page the scan did not read"),
        ("فروشگاه کفش", BASE, "navigational", "y"),
    )
    kept = parse_keyword_answer(raw, pages=PAGES, identity=_identity)
    assert [(item.keyword, item.url) for item in kept] == [("کفش ورزشی", f"{BASE}a"), ("فروشگاه کفش", BASE)]


def test_an_answer_gives_one_page_three_words_at_most():
    raw = answer(*[(f"کفش {number}", BASE, "commercial", "") for number in range(6)])
    assert len(parse_keyword_answer(raw, pages=PAGES, identity=_identity)) == 3


def test_an_answer_that_is_not_the_schema_is_asked_again_then_given_up():
    model = FakeModel("not json", "still not json")
    with pytest.raises(KeywordSuggestionError):
        KeywordSuggester(transport=model).suggest(description="", seeds=[], pages=PAGES, identity=_identity)
    assert model.calls == 2


# Suggesting, keeping and adding words.


def test_words_are_for_pages_the_scan_read_so_the_scan_comes_first():
    world = World()
    world.call("POST", "/suggest", expect=409)
    world.call("POST", expect=409, keyword="کفش")


def test_a_suggestion_comes_back_waiting_for_the_customer():
    world = World()
    world.scan()
    world.call("PUT", "/brief", description="  فروشگاه کفش ورزشی  ", seeds=["کفش ورزشی", "  "])

    keywords = world.suggest()

    assert keywords.description == "فروشگاه کفش ورزشی" and keywords.seeds == ["کفش ورزشی"]
    assert keywords.suggestion_status == SuggestionStatus.IDLE
    assert [(item.keyword, item.url, item.status) for item in keywords.keywords] == [
        ("کفش ورزشی", f"{BASE}a", KeywordStatus.SUGGESTED)
    ]


def test_suggesting_again_keeps_what_the_customer_decided():
    world = World(model=FakeModel(
        answer(("کفش ورزشی", f"{BASE}a", "transactional", ""), ("کفش ارزان", BASE, "commercial", "")),
        answer(("کفش ورزشی", f"{BASE}a", "commercial", "new reason"), ("کفش اصل", BASE, "commercial", "")),
    ))
    world.scan()
    world.suggest()
    world.call("POST", "/status", keyword="کفش ورزشی", status="confirmed")

    world.call("POST", "/suggest", expect=429)
    world.now += timedelta(minutes=11)
    keywords = world.suggest()

    by_word = {item.keyword: item for item in keywords.keywords}
    assert set(by_word) == {"کفش ورزشی", "کفش اصل"}, "an undecided idea gives way to the new ones"
    assert by_word["کفش ورزشی"].status == KeywordStatus.CONFIRMED and by_word["کفش ورزشی"].reason is None


def test_a_model_that_fails_leaves_the_list_as_it_was():
    world = World(model=FakeModel(RuntimeError("down")))
    world.scan()
    keywords = world.suggest()
    assert keywords.suggestion_status == SuggestionStatus.FAILED and keywords.keywords == []


def test_a_word_of_the_customers_own_is_kept_for_a_page_the_scan_read():
    world = World()
    world.scan()
    world.call("POST", expect=422, keyword="کفش", url="https://other.example/")
    added = world.call("POST", expect=201, keyword=" کفش   مردانه ", url=f"{BASE}a")
    assert added["keywords"] == [
        {**added["keywords"][0], "keyword": "کفش مردانه", "url": f"{BASE}a", "status": "confirmed", "source": "customer"}
    ]


def test_the_kept_words_have_a_ceiling():
    world = World()
    world.scan()
    for number in range(MAX_CONFIRMED):
        world.call("POST", expect=201, keyword=f"کفش {number}")
    world.call("POST", expect=409, keyword="یکی بیشتر")


def test_the_ceiling_is_the_plans():
    from dataclasses import replace
    from types import SimpleNamespace

    from app.billing.plans import UNLIMITED

    class Gold:
        def limits(self, tenant_id):
            return replace(UNLIMITED, targets=MAX_CONFIRMED + 5)

        def require_active(self, tenant_id):
            return SimpleNamespace(active=True)

    world = World()
    world.client.app.state.dependencies.billing = Gold()
    world.scan()
    for number in range(MAX_CONFIRMED + 5):
        world.call("POST", expect=201, keyword=f"کفش {number}")
    world.call("POST", expect=409, keyword="یکی بیشتر")


def test_after_a_smaller_plan_only_the_words_it_pays_for_are_looked_up():
    from app.targets.handler import RankTracker

    world = World()
    world.scan()
    for number in range(5):
        world.call("POST", expect=201, keyword=f"کفش {number}")
    tracker = RankTracker(
        target_store=world.targets, site_store=world.sites, serp_provider=world.serp,
        clock=lambda: world.now, targets_of=lambda tenant_id: 2,
    )
    tracker.run_round()
    kept = world.client.get("/v1/sites/site-1/target-keywords").json()["keywords"]
    looked_up = [item["keyword"] for item in kept if item.get("checks")]
    assert looked_up == ["کفش 0", "کفش 1"]


def test_another_customer_sees_none_of_it():
    world = World()
    world.sites.create(Site(site_id="site-2", principal_id="principal-2", name="n", base_url="https://other.example/"))
    assert world.client.get("/v1/sites/site-2/target-keywords").status_code == 404


# A title for a word the page does not rank for yet.


def test_a_title_for_a_new_page_is_compared_with_google_and_waits_for_approval():
    world = World()
    world.scan()
    world.suggest()
    world.call("POST", "/title", expect=409, keyword="کفش ورزشی")  # not kept yet
    world.call("POST", "/status", keyword="کفش ورزشی", status="confirmed")

    run = world.call("POST", "/title", expect=202, keyword="کفش ورزشی")

    finished = world.runs.get(run["run_id"])
    assert finished.result["title_proposal_status"] == "completed", finished.result
    action = world.actions.find(finished.result["action_id"])
    assert action.action.parameters["recommended_title"] == "خرید کفش ورزشی اصل در مدل‌های گوناگون"
    assert action.status.value == "awaiting_approval"
    assert world.serp.asked == ["کفش ورزشی"]


def test_a_site_not_connected_cannot_ask_for_a_title():
    world = World(adapter_type=None)
    world.scan()
    world.call("POST", expect=201, keyword="کفش ورزشی")
    world.call("POST", "/title", expect=409, keyword="کفش ورزشی")


def test_only_a_chosen_word_is_spared_the_rule_that_the_page_must_already_rank():
    class Run:
        run_id, site_id, normalized_url, normalized_query = "run-1", "site-1", f"{BASE}a", "کفش ورزشی"

    chosen = target_strategy(run=Run(), now=datetime(2026, 11, 1, tzinfo=timezone.utc))
    ordinary = chosen.model_copy(update={"evidence": {}})

    def decide(strategy):
        investigation = build_serp_investigation(
            strategy=strategy, primary_queries=[strategy.normalized_query],
            query_snapshots=[SERP().search(query=strategy.normalized_query)],
            investigation_id="i", target_title="کفش",
        )
        return build_serp_decision(strategy=strategy, investigation=investigation, decision_id="d")

    assert decide(chosen).status == SERPDecisionStatus.PASS
    assert decide(ordinary).status == SERPDecisionStatus.INVESTIGATE
    assert "target_not_found_in_serp" in decide(ordinary).reasons


# Where each kept word stands, week by week.


def test_positions_are_looked_up_weekly_for_kept_words_only():
    world = World(serp=SERP(own=(7, f"{BASE}a")))
    world.scan()
    world.suggest()  # "کفش ورزشی", not kept
    world.call("POST", expect=201, keyword="کفش مردانه", url=f"{BASE}a")

    world.serp.fetched_at = world.now
    assert world.tracker().run_round() == {"checked": 1}
    assert world.tracker().run_round() == {"checked": 0}, "not again within the week"
    world.now += timedelta(days=7)
    world.serp.fetched_at = world.now
    world.serp.own = None
    world.tracker().run_round()

    item = next(item for item in world.targets.find("site-1").keywords if item.keyword == "کفش مردانه")
    assert [(check.position, check.url) for check in item.checks] == [(7, f"{BASE}a"), (None, None)]
    assert world.serp.asked == ["کفش مردانه", "کفش مردانه"]


def test_a_round_stops_where_google_stops_answering():
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش مردانه")
    world.serp.fail = True
    assert world.tracker().run_round() == {"checked": 0}
    world.serp.fail = False
    assert world.tracker().run_round() == {"checked": 1}, "picked up next round"


def test_a_removed_site_is_not_looked_up():
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش مردانه")
    site = world.sites.get("site-1")
    world.sites.update(site.model_copy(update={"status": SiteStatus.REMOVED}))
    assert world.tracker().run_round() == {"checked": 0}


# The review: cost, stale answers, lost jobs and lost writes.


def test_a_suggestion_lost_to_a_restart_is_reported_and_can_be_asked_again():
    world = World()
    world.scan()
    world.call("POST", "/suggest", expect=202, run=False)  # the worker dies before it runs

    assert world.call("GET", run=False)["suggestion_status"] == "running"
    world.now += timedelta(minutes=16)
    shown = world.call("GET", run=False)
    assert shown["suggestion_status"] == "failed" and shown["suggestion_error"] == "lost"
    world.call("POST", "/suggest", expect=202)
    assert world.targets.find("site-1").suggestion_status == SuggestionStatus.IDLE


def test_an_older_job_does_not_write_over_a_newer_request():
    world = World()
    world.scan()
    world.call("POST", "/suggest", expect=202, run=False)
    old_job = world.targets.find("site-1").suggestion_job_id
    world.now += timedelta(minutes=16)
    world.call("POST", "/suggest", expect=202, run=False)

    handler = build_target_suggestion_handler(
        site_store=world.sites, scan_store=world.scans, target_store=world.targets,
        suggester=KeywordSuggester(transport=world.model),
    )
    assert handler({"site_id": "site-1", "job_id": old_job}) == {"site_id": "site-1", "skipped": True}
    assert world.targets.find("site-1").suggestion_status == SuggestionStatus.RUNNING


def test_a_failed_suggestion_still_waits_out_the_cooldown():
    world = World(model=FakeModel(RuntimeError("down")))
    world.scan()
    world.suggest()
    world.call("POST", "/suggest", expect=429)


def test_a_tenant_has_a_daily_number_of_suggestions(monkeypatch):
    monkeypatch.setattr("app.api.target_routes.SUGGESTS_PER_TENANT_PER_DAY", 2)
    world = World()
    world.scan()
    world.suggest()
    world.now += timedelta(minutes=11)
    world.suggest()
    world.now += timedelta(minutes=11)
    world.call("POST", "/suggest", expect=429)
    world.now += timedelta(days=1)
    world.suggest()


def test_one_title_at_a_time_per_word_then_a_wait():
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش ورزشی", url=f"{BASE}a")

    world.call("POST", "/title", expect=202, run=False, keyword="کفش ورزشی")
    world.call("POST", "/title", expect=409, run=False, keyword="کفش ورزشی")
    world.run_jobs()
    world.call("POST", "/title", expect=429, keyword="کفش ورزشی")
    world.now += timedelta(minutes=31)
    world.call("POST", "/title", expect=202, keyword="کفش ورزشی")


def test_a_tenant_has_a_daily_number_of_titles(monkeypatch):
    monkeypatch.setattr("app.api.target_routes.TITLES_PER_TENANT_PER_DAY", 1)
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش ورزشی", url=f"{BASE}a")
    world.call("POST", expect=201, keyword="کفش مردانه", url=f"{BASE}a")
    world.call("POST", "/title", expect=202, keyword="کفش ورزشی")
    world.call("POST", "/title", expect=429, keyword="کفش مردانه")


def test_a_retried_title_run_does_not_ask_google_and_the_model_again():
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش ورزشی", url=f"{BASE}a")
    create = world.actions.create
    failures = iter([RuntimeError("database blip")])

    def flaky(managed):
        for error in failures:
            raise error
        return create(managed)

    world.actions.create = flaky
    run = world.call("POST", "/title", expect=202, keyword="کفش ورزشی")

    finished = world.runs.get(run["run_id"])
    assert finished.status.value == "completed" and finished.result["action_id"]
    assert world.serp.asked == ["کفش ورزشی"], "the second attempt used the proposal the first one paid for"


def test_a_title_run_is_for_the_page_as_every_stored_address_is_written():
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش ورزشی", url=f"{BASE}a")
    run = world.call("POST", "/title", expect=202, keyword="کفش ورزشی")
    assert world.runs.get(run["run_id"]).normalized_url == canonicalize_url(f"{BASE}a")


def test_an_old_answer_from_the_cache_is_not_recorded_as_this_weeks():
    world = World(serp=SERP(own=(7, f"{BASE}a")))
    world.scan()
    world.call("POST", expect=201, keyword="کفش مردانه", url=f"{BASE}a")
    world.serp.fetched_at = world.now - timedelta(days=3)
    world.tracker().run_round()
    world.now += timedelta(days=7)
    world.serp.own = (2, f"{BASE}a")  # what Google would say now, were it asked
    world.tracker().run_round()  # the cache still hands back the old answer

    item = world.targets.find("site-1").keywords[0]
    assert [(check.checked_at, check.position) for check in item.checks] == [(world.serp.fetched_at, 7)]


def test_a_change_made_meanwhile_is_not_undone():
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش ورزشی", url=f"{BASE}a")
    read = world.targets.read_or_new

    def read_then_someone_writes(site):
        stale = read(site)
        current, version = world.targets.read(site.site_id)
        world.targets.save(current.model_copy(update={"description": "از جای دیگر"}), expected_version=version)
        return stale

    world.targets.read_or_new = read_then_someone_writes
    world.call("POST", "/status", expect=409, keyword="کفش ورزشی", status="dismissed")
    assert world.targets.find("site-1").keywords[0].status == KeywordStatus.CONFIRMED


def test_a_job_redoes_its_change_on_the_set_as_it_is_now():
    world = World()
    world.scan()
    world.call("POST", expect=201, keyword="کفش مردانه", url=f"{BASE}a")
    world.serp.fetched_at = world.now
    search = world.serp.search

    def search_while_the_customer_adds(*, query):
        world.call("POST", expect=201, keyword="کفش زنانه", url=f"{BASE}a")
        return search(query=query)

    world.serp.search = search_while_the_customer_adds
    world.tracker(per_round=1).run_round()
    keywords = {item.keyword: item for item in world.targets.find("site-1").keywords}
    assert set(keywords) == {"کفش مردانه", "کفش زنانه"} and len(keywords["کفش مردانه"].checks) == 1


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/brief", {"description": "x"}),
        ("POST", "/suggest", {}),
        ("POST", "", {"keyword": "کفش"}),
        ("POST", "/status", {"keyword": "کفش", "status": "confirmed"}),
        ("POST", "/title", {"keyword": "کفش"}),
    ],
)
def test_another_customers_site_cannot_be_changed(method, path, body):
    world = World()
    world.sites.create(Site(site_id="site-2", principal_id="principal-2", name="n", base_url="https://other.example/"))
    world.call(method, path, expect=404, site="site-2", **body)
    assert world.targets.find("site-2") is None


def test_a_word_added_again_keeps_where_it_stood():
    world = World(serp=SERP(own=(7, f"{BASE}a")))
    world.scan()
    world.call("POST", expect=201, keyword="کفش مردانه", url=f"{BASE}a")
    world.serp.fetched_at = world.now
    world.tracker().run_round()
    world.call("POST", "/status", keyword="کفش مردانه", status="dismissed")
    world.call("POST", expect=201, keyword="کفش مردانه", url=BASE)
    item = world.targets.find("site-1").keywords[0]
    assert item.status == KeywordStatus.CONFIRMED and item.url == BASE and len(item.checks) == 1


def test_a_full_list_forgets_its_oldest_dismissed_words_instead_of_locking_the_site(monkeypatch):
    monkeypatch.setattr("app.targets.handler.MAX_WORDS", 3)
    world = World()
    world.scan()
    for word in ("یک", "دو"):
        world.call("POST", expect=201, keyword=word)
        world.call("POST", "/status", keyword=word, status="dismissed")
    world.call("POST", expect=201, keyword="سه")
    world.call("POST", expect=201, keyword="چهار")

    assert [item.keyword for item in world.targets.find("site-1").keywords] == ["دو", "سه", "چهار"]


def test_a_full_list_still_takes_new_suggestions(monkeypatch):
    monkeypatch.setattr("app.targets.handler.MAX_WORDS", 2)
    world = World()
    world.scan()
    for word in ("یک", "دو"):
        world.call("POST", expect=201, keyword=word)
        world.call("POST", "/status", keyword=word, status="dismissed")

    keywords = world.suggest()

    assert [(item.keyword, item.status) for item in keywords.keywords] == [
        ("دو", KeywordStatus.DISMISSED), ("کفش ورزشی", KeywordStatus.SUGGESTED)
    ]


def test_a_round_with_nothing_new_writes_nothing():
    world = World(serp=SERP(own=(7, f"{BASE}a")))
    world.scan()
    world.call("POST", expect=201, keyword="کفش مردانه", url=f"{BASE}a")
    world.serp.fetched_at = world.now
    world.tracker().run_round()
    _, version = world.targets.read("site-1")
    world.now += timedelta(days=7)  # due again, but the cache has nothing newer
    world.tracker().run_round()
    assert world.targets.read("site-1")[1] == version


def test_one_bad_word_in_an_answer_does_not_cost_the_rest():
    raw = answer(
        ("کلمه " * 30, BASE, "commercial", ""),
        ("کفش ورزشی", f"{BASE}a", "transactional", "x" * 400),
        ("کفش", BASE, "commercial", ""),
    )
    assert [item.keyword for item in parse_keyword_answer(raw, pages=PAGES, identity=_identity)] == ["کفش"]



# Competitors: from the same weekly search, for the gold plan.


def test_where_competitors_stand_comes_from_the_same_search():
    from app.models.serp import SERPResultItem
    from app.targets.handler import standings

    results = [
        SERPResultItem(position=1, url="https://www.a.example/1", title="x"),
        SERPResultItem(position=2, url="https://a.example/2", title="x"),  # the same site again
        SERPResultItem(position=3, url=f"{BASE}a", title="x"),  # the customer's own
        SERPResultItem(position=4, url="https://b.example/", title="x"),
        SERPResultItem(position=5, url="https://c.example/", title="x"),
        SERPResultItem(position=6, url="http://[broken", title="x"),  # an address that cannot be read
    ]
    rivals, leaders = standings(results, BASE, ["b.example", "gone.example"])
    assert [(r.host, r.position) for r in rivals] == [("b.example", 4)], "one not found is not kept"
    assert [(r.host, r.position) for r in leaders] == [("a.example", 1), ("b.example", 4), ("c.example", 5)]


def keep_two_words(world):
    world.scan()
    for keyword in ("کفش ۱", "کفش ۲"):
        world.call("POST", expect=201, keyword=keyword)


def test_the_weekly_look_keeps_competitors_and_only_the_latest_first_three():
    world = World()
    keep_two_words(world)
    assert world.call("PUT", "/competitors", competitors=["https://www.b.example/shop", "c.example"])["competitors"] == ["b.example", "c.example"]
    world.tracker().run_round()
    world.serp.fetched_at += timedelta(days=8)
    world.now += timedelta(days=8)
    world.tracker().run_round()
    checks = world.targets.find("site-1").keywords[0].checks
    assert [(r.host, r.position) for r in checks[-1].rivals] == [("b.example", 2), ("c.example", 3)]
    assert [r.host for r in checks[-1].leaders] == ["a.example", "b.example", "c.example"]
    assert checks[0].rivals and checks[0].leaders == [], "an old week keeps its competitors, not its first three"
    assert checks[-1].watched == ["b.example", "c.example"]


def test_a_plan_without_competitors_neither_keeps_nor_sets_them():
    from dataclasses import replace
    from types import SimpleNamespace

    from app.billing.plans import UNLIMITED

    class Basic:
        def limits(self, tenant_id):
            return replace(UNLIMITED, competitors=False)

        def require_active(self, tenant_id):
            return SimpleNamespace(active=True)

    world = World()
    keep_two_words(world)
    world.client.app.state.dependencies.billing = Basic()
    assert world.client.get("/v1/sites/site-1/target-keywords").json()["competitors_allowed"] is False
    refused = world.client.put("/v1/sites/site-1/target-keywords/competitors", json={"competitors": ["b.example"]})
    assert refused.status_code == 402
    world.tracker(rivals_of=lambda tenant_id: False).run_round()
    check = world.targets.find("site-1").keywords[0].checks[-1]
    assert check.rivals == [] and check.leaders == []


def test_a_competitor_must_be_another_site():
    world = World()
    world.scan()
    for typed in ("not a site", "https://", "a..example"):
        assert world.client.put("/v1/sites/site-1/target-keywords/competitors", json={"competitors": [typed]}).status_code == 422, typed
    own = world.client.put("/v1/sites/site-1/target-keywords/competitors", json={"competitors": [BASE]})
    assert own.status_code == 422 and "this site itself" in own.json()["detail"]
    too_many = world.client.put("/v1/sites/site-1/target-keywords/competitors", json={"competitors": ["a.ir", "b.ir", "c.ir", "d.ir"]})
    assert too_many.status_code == 422



def test_a_name_in_persian_letters_is_the_same_site_as_in_googles_results():
    from app.api.target_routes import competitor_host
    from app.targets.handler import host_of_url

    assert competitor_host("کفش.ir") == host_of_url("https://xn--zgbzz0a.ir/shop") == "xn--zgbzz0a.ir"
    assert competitor_host("WWW.Example.COM:8080/page") == "example.com"
    assert competitor_host("http://[1.2.3.4") is None  # not a 500


def test_competitors_kept_under_gold_are_not_shown_after_a_downgrade():
    from dataclasses import replace
    from types import SimpleNamespace

    from app.billing.plans import UNLIMITED

    world = World()
    keep_two_words(world)
    world.call("PUT", "/competitors", competitors=["b.example"])
    world.tracker().run_round()

    class Basic:
        def limits(self, tenant_id):
            return replace(UNLIMITED, competitors=False)

        def require_active(self, tenant_id):
            return SimpleNamespace(active=True)

    world.client.app.state.dependencies.billing = Basic()
    shown = world.client.get("/v1/sites/site-1/target-keywords").json()
    assert all(not check["rivals"] and not check["leaders"] for item in shown["keywords"] for check in item["checks"])
    # Nor in what any other answer carries.
    answered = world.call("PUT", "/brief", description="کفش", seeds=[])
    assert all(not check["rivals"] and not check["watched"] for item in answered["keywords"] for check in item["checks"])
    assert world.targets.find("site-1").keywords[0].checks[-1].rivals, "kept, for when the plan has them again"
