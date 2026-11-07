"""The site scan: reading a site from outside, what it finds, and the two files it proposes."""
import gzip
import threading
import time
from datetime import datetime, timedelta, timezone
from http.client import BadStatusLine, IncompleteRead, InvalidURL

import pytest
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.api.scan_routes import SCANS_PER_TENANT_PER_DAY
from app.scan import crawler
from app.scan.files import plan_site_files
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.site_files import SiteFileChange, SiteFileKind, SiteFileStatus
from app.models.site_scan import ScannedPage, ScanStatus, SiteScan
from app.models.sites import Site, SiteAdapterConnection
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.scan.crawler import Fetched, SiteCrawler, parse_sitemap
from app.scan.files import build_sitemap, improve_robots, improved_list
from app.scan.robots_rules import google_allows
from app.scan.handler import (
    SITE_FILE_JOB_TYPE,
    SITE_SCAN_JOB_TYPE,
    build_site_file_handler,
    build_site_scan_handler,
)
from app.scan.issues import build_issues, is_listable, robots_blocks_everything
from app.scan.store import SiteFileStore, SiteScanStore
from app.site_adapters.hoshyarseo import HoshyarConnectorError

BASE = "https://shop.example/"
API_KEY = "scan-secret"
GOOD_TITLE = "فروشگاه نمونه با عنوانی کامل و کافی"


def html(title=GOOD_TITLE, *, links=(), body="کلمه " * 150, head=""):
    anchors = "".join(f'<a href="{link}">x</a>' for link in links)
    return (
        f"<html><head><title>{title}</title><meta name='description' content='توضیح صفحه'>{head}</head>"
        f"<body><h1>سرتیتر</h1>{body}{anchors}</body></html>"
    ).encode("utf-8")


class FakeSite:
    def __init__(self, pages, *, robots=None, robots_status=None, sitemaps=None):
        self.pages = dict(pages)
        self.robots = robots
        self.robots_status = robots_status
        self.sitemaps = dict(sitemaps or {})
        self.fetched = []

    def __call__(self, url, limit):
        self.fetched.append(url)
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        if path == "/robots.txt":
            if self.robots_status:
                return Fetched(status=self.robots_status)
            if self.robots is None:
                return Fetched(status=404)
            return Fetched(status=200, body=self.robots.encode("utf-8"), content_type="text/plain")
        if path in self.sitemaps:
            return Fetched(status=200, body=self.sitemaps[path], content_type="application/xml", final_url=url)
        if path in self.pages:
            answer = self.pages[path]
            if isinstance(answer, Fetched):
                return answer
            return Fetched(status=200, body=answer, final_url=url, content_type="text/html; charset=utf-8")
        return Fetched(status=404, final_url=url)


def crawl(site, **options):
    return SiteCrawler(fetcher=site, delay_seconds=0, sleep=lambda _: None, **options).crawl(BASE)


def urlset(*paths):
    locations = "".join(f"<url><loc>https://shop.example{path}</loc></url>" for path in paths)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locations}</urlset>'.encode()


def codes(issues, url=None):
    return {issue.code for issue in issues if url is None or issue.url == url}


# ---- finding the pages -------------------------------------------------------


def test_a_site_with_no_sitemap_is_read_by_following_its_links():
    site = FakeSite({
        "/": html(links=["/a", "b", "https://shop.example/c#top", "https://other.example/x", "mailto:a@b.c", "/photo.jpg"]),
        "/a": html("صفحهٔ الف با عنوانی کافی", links=["/d"]),
        "/b": html("صفحهٔ ب با عنوانی کافی"),
        "/c": html("صفحهٔ ج با عنوانی کافی"),
        "/d": html("صفحهٔ د با عنوانی کافی"),
    })

    result = crawl(site)

    assert sorted(page.url for page in result.pages) == [f"{BASE}{p}" for p in ("", "a", "b", "c", "d")]
    assert result.sitemap_urls == []
    assert not any("other.example" in url or url.endswith(".jpg") for url in site.fetched)
    assert "no_sitemap" in codes(build_issues(result))


def test_a_sitemap_named_in_robots_is_used_and_its_pages_are_marked():
    site = FakeSite(
        {"/": html(links=["/unlisted"]), "/listed": html("صفحهٔ فهرست‌شده با عنوان کافی"), "/unlisted": html("صفحهٔ بیرون از فهرست با عنوان")},
        robots="User-agent: *\nDisallow:\n\nSitemap: https://shop.example/map.xml\n",
        sitemaps={"/map.xml": urlset("/", "/listed")},
    )

    result = crawl(site)

    assert result.sitemap_urls == ["https://shop.example/map.xml"]
    assert result.sitemap_page_count == 2
    pages = {page.url: page for page in result.pages}
    assert pages[f"{BASE}listed"].in_sitemap and not pages[f"{BASE}unlisted"].in_sitemap
    assert "not_in_sitemap" in codes(build_issues(result), f"{BASE}unlisted")


def test_a_sitemap_is_found_at_its_usual_address_and_through_an_index():
    index = (
        b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://shop.example/posts.xml.gz</loc></sitemap></sitemapindex>"
    )
    site = FakeSite(
        {"/": html(), "/post": html("نوشته‌ای با عنوان کاملاً کافی")},
        sitemaps={"/sitemap.xml": index, "/posts.xml.gz": gzip.compress(urlset("/post"))},
    )

    result = crawl(site)

    assert result.sitemap_urls == ["https://shop.example/sitemap.xml"]
    assert any(page.url == f"{BASE}post" and page.in_sitemap for page in result.pages)
    assert "sitemap_not_in_robots" in codes(build_issues(result))


def test_the_home_page_is_read_once_though_the_sitemap_lists_it_too():
    site = FakeSite({"/": html()}, sitemaps={"/sitemap.xml": urlset("", "/")})
    assert [page.url for page in crawl(site).pages] == [BASE]


def test_the_same_page_with_a_query_string_is_not_read_again():
    site = FakeSite({
        "/": html(links=["/post/", "/post/?replytocom=1", "/post/?orderby=date", "/list?id=1"]),
        "/post/": html("نوشته‌ای با عنوان کاملاً کافی"),
        "/list?id=1": html("صفحه‌ای که فقط با پارامتر هست"),
    })
    assert sorted(page.url for page in crawl(site).pages) == [BASE, f"{BASE}list?id=1", f"{BASE}post/"]


def test_a_sitemap_on_another_host_is_not_followed():
    site = FakeSite({"/": html()}, robots="Sitemap: https://evil.example/map.xml\n")
    result = crawl(site)
    assert not any("evil.example" in url for url in site.fetched)
    assert result.sitemap_urls == []


@pytest.mark.parametrize(
    "body",
    [
        b'<!DOCTYPE lolz [<!ENTITY lol "lol">]><urlset><url><loc>&lol;</loc></url></urlset>',
        b"<urlset><url><loc>https://shop.example/a</urlset>",
        b"<html><body>not a sitemap</body></html>",
        b"",
    ],
)
def test_anything_but_a_plain_sitemap_is_no_sitemap(body):
    assert parse_sitemap(body) == ([], [])


def test_a_compressed_sitemap_cannot_inflate_without_limit():
    bomb = gzip.compress(b"<urlset>" + b" " * 30_000_000 + b"</urlset>")
    assert parse_sitemap(bomb) == ([], [])


def test_what_robots_forbids_google_is_not_read_and_is_reported():
    site = FakeSite(
        {"/": html(links=["/private/x", "/open"]), "/open": html("صفحهٔ باز با عنوانی کاملاً کافی")},
        robots="User-agent: *\nDisallow: /private/\n",
    )
    result = crawl(site)
    assert f"{BASE}private/x" not in site.fetched
    assert next(page for page in result.pages if page.url == f"{BASE}private/x").robots_blocked


def test_a_scan_stops_at_its_page_limit_and_says_so():
    site = FakeSite({"/": html(links=[f"/p{i}" for i in range(20)]), **{f"/p{i}": html(f"صفحهٔ شمارهٔ {i} با عنوان کافی") for i in range(20)}})
    result = crawl(site, max_pages=5)
    assert len(result.pages) == 5
    assert result.pages_capped


def test_a_scan_stops_at_its_time_limit():
    ticks = iter(range(0, 10_000, 100))
    site = FakeSite({"/": html(links=[f"/p{i}" for i in range(50)]), **{f"/p{i}": html() for i in range(50)}})
    result = SiteCrawler(fetcher=site, sleep=lambda _: None, time_budget_seconds=500, clock=lambda: next(ticks)).crawl(BASE)
    assert result.pages_capped
    assert len(result.pages) < 20


def test_a_file_that_is_not_a_page_is_not_counted_as_one():
    site = FakeSite({"/": html(links=["/report"]), "/report": Fetched(status=200, body=b"%PDF", content_type="application/pdf")})
    assert [page.url for page in crawl(site).pages] == [BASE]


# ---- what it finds -----------------------------------------------------------


def test_a_site_that_keeps_google_out_is_the_first_thing_reported():
    site = FakeSite({"/": html()}, robots="User-agent: *\nDisallow: /\n")
    result = crawl(site)
    issues = build_issues(result)
    assert issues[0].severity == "critical"
    assert "robots_blocks_everything" in codes(issues)
    assert "home_unreachable" not in codes(issues), "it is reachable; it is keeping Google out"


def test_the_rule_keeping_google_out_gets_a_proposal_to_remove_it():
    world = World(FakeSite({"/": html()}, robots="User-agent: *\nDisallow: /\n"))
    world.scan()
    change = world.files.find("site-1", SiteFileKind.ROBOTS)
    assert "removed_block_all" in change.changes
    assert not robots_blocks_everything(change.proposed_text)
    assert change.previous_text == "User-agent: *\nDisallow: /\n"


@pytest.mark.parametrize(
    ("text", "blocked"),
    [
        ("User-agent: *\nDisallow: /", True),
        ("User-agent: Googlebot\nDisallow: /", True),
        ("User-agent: BadBot\nDisallow: /", False),
        ("User-agent: BadBot\nUser-agent: *\nDisallow: /", True),
        ("User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nDisallow:", False),
        ("User-agent: *\nDisallow: /admin/", False),
        ("# User-agent: *\n# Disallow: /", False),
        (None, False),
    ],
)
def test_keeping_everyone_out_is_told_from_keeping_someone_out(text, blocked):
    assert robots_blocks_everything(text) is blocked


def test_a_server_error_on_robots_is_critical_as_google_stays_away_then():
    result = crawl(FakeSite({"/": html()}, robots_status=503))
    issues = build_issues(result)
    assert "robots_error" in codes(issues)
    assert "no_robots" not in codes(issues)


def test_page_problems_are_named():
    site = FakeSite({
        "/": html(links=["/noindex", "/long", "/none", "/elsewhere", "/broken", "/thin"]),
        "/noindex": html("صفحه‌ای که نباید ایندکس شود و عنوان دارد", head="<meta name='robots' content='noindex, follow'>"),
        "/long": html("عنوان " * 30),
        "/none": b"<html><head></head><body><p>hello</p></body></html>",
        "/elsewhere": html("صفحه‌ای با canonical به جای دیگر", head="<link rel='canonical' href='https://shop.example/other'>"),
        "/broken": Fetched(status=404, final_url=f"{BASE}broken"),
        "/thin": html("صفحهٔ کم‌محتوا با عنوانی که کافی است", body="کم"),
    })
    issues = build_issues(crawl(site))

    assert "noindex" in codes(issues, f"{BASE}noindex")
    assert "title_too_long" in codes(issues, f"{BASE}long")
    assert {"missing_title", "missing_description", "missing_h1"} <= codes(issues, f"{BASE}none")
    assert "canonical_elsewhere" in codes(issues, f"{BASE}elsewhere")
    assert "broken_page" in codes(issues, f"{BASE}broken")
    assert "thin_content" in codes(issues, f"{BASE}thin")


def test_a_header_can_say_noindex_too():
    site = FakeSite({"/": Fetched(status=200, body=html(), content_type="text/html", x_robots="noindex", final_url=BASE)})
    assert crawl(site).pages[0].noindex


def test_noindex_on_a_page_the_sitemap_lists_is_critical():
    site = FakeSite(
        {"/": html(head="<meta name='robots' content='noindex'>")},
        sitemaps={"/sitemap.xml": urlset("/")},
    )
    issue = next(item for item in build_issues(crawl(site)) if item.code == "noindex_in_sitemap")
    assert issue.severity == "critical"


def test_the_same_title_on_two_pages_is_reported_on_both():
    site = FakeSite({"/": html(links=["/a"]), "/a": html()})
    issues = build_issues(crawl(site))
    assert "duplicate_title" in codes(issues, BASE)
    assert "duplicate_title" in codes(issues, f"{BASE}a")


def test_the_list_is_the_same_each_time_for_the_same_site():
    site = FakeSite({"/": html(links=["/a", "/b"]), "/a": html(), "/b": html("x")})
    assert build_issues(crawl(site)) == build_issues(crawl(site))


# ---- the two files -----------------------------------------------------------


def page(url, **fields):
    return ScannedPage(url=url, **{"status_code": 200, "title": "t", **fields})


def test_a_sitemap_lists_only_pages_that_belong_in_one():
    pages = [
        page(BASE),
        page(f"{BASE}a?x=1&y=2"),
        page(f"{BASE}noindex", noindex=True),
        page(f"{BASE}blocked", robots_blocked=True, status_code=None),
        page(f"{BASE}gone", status_code=404, title=None),
        page(f"{BASE}moved", redirected_to=f"{BASE}a"),
        page(f"{BASE}copy", canonical=BASE),
    ]
    assert [item.url for item in pages if is_listable(item)] == [BASE, f"{BASE}a?x=1&y=2"]
    text = build_sitemap([item.url for item in pages if is_listable(item)])
    assert "https://shop.example/a?x=1&amp;y=2" in text, "escaped for XML"
    assert parse_sitemap(text.encode("utf-8"))[0] == [BASE, f"{BASE}a?x=1&y=2"]


def test_no_page_to_list_is_no_sitemap():
    assert build_sitemap([]) is None


def test_a_site_without_robots_gets_one_that_keeps_nobody_out():
    text, changes = improve_robots(None, sitemap_url="https://shop.example/sitemap.xml")
    assert changes == ["created", "added_sitemap"]
    assert text == "User-agent: *\nDisallow:\n\nSitemap: https://shop.example/sitemap.xml\n"
    assert not robots_blocks_everything(text)


def test_what_robots_already_says_stays_line_for_line():
    current = "# ours\nUser-agent: *\nDisallow: /cart/\nAllow: /cart/public\n\nUser-agent: BadBot\nDisallow: /\n"
    text, changes = improve_robots(current, sitemap_url="https://shop.example/sitemap.xml")
    assert changes == ["added_sitemap"]
    assert text.startswith(current.rstrip("\n"))
    assert text.endswith("Sitemap: https://shop.example/sitemap.xml\n")


def test_a_robots_with_nothing_missing_is_left_alone():
    current = "User-agent: *\nDisallow:\n\nSitemap: https://shop.example/sitemap.xml\n"
    assert improve_robots(current, sitemap_url="https://shop.example/sitemap.xml") == (current, [])


def test_the_rule_keeping_google_out_is_the_only_rule_removed_and_it_is_named():
    current = "User-agent: *\nDisallow: /\n\nUser-agent: BadBot\nDisallow: /\n"
    text, changes = improve_robots(current, sitemap_url=None)
    assert changes == ["removed_block_all"]
    assert text == "User-agent: *\nDisallow:\n\nUser-agent: BadBot\nDisallow: /\n"
    assert not robots_blocks_everything(text)


# ---- the jobs ----------------------------------------------------------------


class World:
    def __init__(self, fake_site, *, adapter_type="manual"):
        self.repository = InMemoryRepository()
        self.sites = SiteStore(self.repository)
        self.scans = SiteScanStore(self.repository)
        self.files = SiteFileStore(self.repository)
        self.fake_site = fake_site
        self.now = datetime.now(timezone.utc)
        connection = SiteAdapterConnection(adapter_type=adapter_type) if adapter_type else None
        self.sites.create(Site(site_id="site-1", principal_id="principal-1", name="n", base_url=BASE, site_adapter=connection))
        handlers = JobHandlerRegistry()
        self.scan_handler = build_site_scan_handler(
            site_store=self.sites, scan_store=self.scans, file_store=self.files,
            crawler_factory=lambda: SiteCrawler(fetcher=fake_site, delay_seconds=0, sleep=lambda _: None),
        )
        self.file_handler = build_site_file_handler(
            site_store=self.sites, file_store=self.files, fetcher=fake_site
        )
        handlers.register(SITE_SCAN_JOB_TYPE, self.scan_handler)
        handlers.register(SITE_FILE_JOB_TYPE, self.file_handler)
        self.jobs = JobStore(self.repository)
        app = create_app(APIDependencies(
            scheduler=JobScheduler(store=self.jobs, handlers=handlers),
            authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
            site_store=self.sites, scan_store=self.scans, file_store=self.files,
            clock=lambda: self.now,
        ))
        self.client = TestClient(app)
        self.client.headers["Authorization"] = f"Bearer {API_KEY}"
        self.scheduler = app.state.dependencies.scheduler

    def run_jobs(self):
        while self.scheduler.run_once() is not None:
            pass

    def scan(self):
        response = self.client.post("/v1/sites/site-1/scans")
        assert response.status_code == 202, response.text
        self.run_jobs()
        return self.client.get("/v1/sites/site-1/scans/latest").json()

    def post(self, kind, verb, expect=202):
        if verb == "check":
            self.now += timedelta(seconds=31)  # past the wait between checks
        response = self.client.post(f"/v1/sites/site-1/site-files/{kind}/{verb}")
        assert response.status_code == expect, response.text
        self.run_jobs()
        return self.files.find("site-1", SiteFileKind(kind))


def new_site():
    return FakeSite({"/": html(links=["/a"]), "/a": html("صفحهٔ الف با عنوانی کاملاً کافی")})


def test_a_scan_starts_from_the_address_alone_and_proposes_both_files():
    world = World(new_site(), adapter_type=None)

    scan = world.scan()

    assert scan["status"] == "completed"
    assert len(scan["pages"]) == 2
    assert {"no_sitemap", "no_robots"} <= {issue["code"] for issue in scan["issues"]}
    sitemap = world.files.find("site-1", SiteFileKind.SITEMAP)
    robots = world.files.find("site-1", SiteFileKind.ROBOTS)
    assert sitemap.status == SiteFileStatus.PROPOSED and sitemap.public_url == f"{BASE}sitemap.xml"
    assert f"{BASE}a" in sitemap.proposed_text
    assert "Sitemap: https://shop.example/sitemap.xml" in robots.proposed_text


def test_a_site_we_could_not_see_gets_no_proposals():
    world = World(FakeSite({"/": Fetched(status=None)}))
    scan = world.scan()
    assert "home_unreachable" in {issue["code"] for issue in scan["issues"]}
    assert world.files.for_site("site-1") == []


def test_a_robots_that_answered_with_an_error_is_not_replaced():
    world = World(FakeSite({"/": html()}, robots_status=503))
    world.scan()
    assert world.files.find("site-1", SiteFileKind.ROBOTS) is None


def test_scans_cannot_be_started_over_each_other_or_in_a_hurry():
    world = World(new_site())
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202
    assert world.client.post("/v1/sites/site-1/scans").status_code == 409, "one is queued"
    world.run_jobs()
    assert world.client.post("/v1/sites/site-1/scans").status_code == 429, "just finished"
    world.now += timedelta(minutes=6)
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202


def test_a_scan_lost_with_a_restart_does_not_hold_the_button_for_ever():
    world = World(new_site())
    world.client.post("/v1/sites/site-1/scans")
    world.now += timedelta(minutes=20)
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202


def test_someone_elses_site_is_not_scanned_or_read():
    world = World(new_site())
    world.sites.create(Site(site_id="site-2", principal_id="principal-2", name="n", base_url=BASE))
    for call in (
        lambda: world.client.post("/v1/sites/site-2/scans"),
        lambda: world.client.get("/v1/sites/site-2/scans/latest"),
        lambda: world.client.get("/v1/sites/site-2/site-files"),
        lambda: world.client.post("/v1/sites/site-2/site-files/robots/approve"),
    ):
        assert call().status_code in {403, 404}


def test_scan_jobs_cannot_be_created_directly():
    world = World(new_site())
    response = world.client.post("/v1/jobs", json={"job_type": SITE_FILE_JOB_TYPE, "payload": {"site_id": "site-1", "kind": "robots"}})
    assert response.status_code in {403, 422}


# Manual: the customer puts the file in place, and we look.


def test_on_a_manual_site_approval_hands_over_the_file_and_nothing_is_written():
    world = World(new_site())
    world.scan()

    change = world.post("sitemap", "approve", expect=200)

    assert change.status == SiteFileStatus.WAITING_FOR_CUSTOMER
    download = world.client.get("/v1/sites/site-1/site-files/sitemap/content")
    assert download.status_code == 200
    assert 'filename="sitemap.xml"' in download.headers["content-disposition"]
    assert download.text == change.proposed_text


def test_the_check_sees_whether_the_customer_has_put_it_there():
    site = new_site()
    world = World(site)
    world.scan()
    world.post("sitemap", "approve", expect=200)

    assert world.post("sitemap", "check").status == SiteFileStatus.WAITING_FOR_CUSTOMER

    site.sitemaps["/sitemap.xml"] = urlset("/", "/a")
    assert world.post("sitemap", "check").status == SiteFileStatus.APPLIED


def test_a_robots_only_counts_as_in_place_when_it_says_what_was_approved():
    site = new_site()
    world = World(site)
    world.scan()
    change = world.post("robots", "approve", expect=200)

    site.robots = "User-agent: *\nDisallow: /\n"
    assert world.post("robots", "check").status == SiteFileStatus.WAITING_FOR_CUSTOMER

    site.robots = change.proposed_text.replace("\n", "\r\n")
    assert world.post("robots", "check").status == SiteFileStatus.APPLIED


def test_a_later_scan_finding_the_files_in_place_proposes_nothing_new():
    site = new_site()
    world = World(site)
    world.scan()
    world.post("sitemap", "approve", expect=200)
    world.post("robots", "approve", expect=200)
    site.sitemaps["/sitemap.xml"] = urlset("/", "/a")
    site.robots = world.files.find("site-1", SiteFileKind.ROBOTS).proposed_text
    world.post("sitemap", "check")
    world.post("robots", "check")

    world.now += timedelta(minutes=6)
    world.scan()

    assert world.files.find("site-1", SiteFileKind.SITEMAP).status == SiteFileStatus.APPLIED
    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.APPLIED


def test_a_refused_proposal_is_not_asked_again_until_there_is_something_new():
    site = new_site()
    world = World(site)
    world.scan()
    world.post("robots", "reject", expect=200)

    world.now += timedelta(minutes=6)
    world.scan()
    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.REJECTED

    site.robots = "User-agent: *\nDisallow: /\n"
    world.now += timedelta(minutes=6)
    world.scan()
    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.PROPOSED


def test_an_existing_sitemap_with_problems_is_improved_on_a_manual_site():
    site = FakeSite(
        {"/": html(links=["/new"]), "/new": html("صفحهٔ تازه با عنوانی کاملاً کافی")},
        sitemaps={"/sitemap.xml": urlset("/", "/gone")},
    )
    world = World(site)
    world.scan()
    change = world.files.find("site-1", SiteFileKind.SITEMAP)
    assert change.changes == ["improved"]
    assert change.public_url == f"{BASE}sitemap.xml"
    assert f"{BASE}new" in change.proposed_text and "gone" not in change.proposed_text


def test_a_sitemap_is_never_replaced_by_the_shorter_list_of_a_scan_that_stopped_early():
    links = [f"/p{i}" for i in range(5)]
    site = FakeSite(
        {"/": html(links=links), **{path: html(f"صفحهٔ {path} با عنوانی کافی") for path in links}},
        sitemaps={"/sitemap.xml": urlset("/", "/gone")},
    )
    world = World(site)
    world.scan_handler = build_site_scan_handler(
        site_store=world.sites, scan_store=world.scans, file_store=world.files,
        crawler_factory=lambda: SiteCrawler(fetcher=site, delay_seconds=0, sleep=lambda _: None, max_pages=2),
    )
    world.client.post("/v1/sites/site-1/scans")
    job = world.scans.find("site-1").job_id
    world.scan_handler({"site_id": "site-1", "job_id": job})
    assert world.files.find("site-1", SiteFileKind.SITEMAP) is None


# Connected WordPress: written through the plugin, and exactly undone.


def test_scan_status_values_are_the_ones_the_page_knows():
    assert {status.value for status in ScanStatus} == {"queued", "running", "completed", "failed"}
    assert isinstance(SiteFileChange.model_fields["kind"].annotation, type)


# ---- what a bad site or a bad moment must not do ----------------------------------


class _Socket:
    """A slow or broken server, behind the real fetcher."""

    def __init__(self, chunks, delay=0.0):
        self.chunks = list(chunks)
        self.delay = delay
        self.status = 200
        self.headers = {"Content-Type": "text/html"}

    def geturl(self):
        return BASE

    def read1(self, _size):
        time.sleep(self.delay)
        return self.chunks.pop(0) if self.chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_a_server_dripping_bytes_cannot_hold_a_fetch_past_its_deadline(monkeypatch):
    monkeypatch.setattr(crawler, "open_public", lambda *a, **k: _Socket([b"x"] * 10_000, delay=0.01))
    started = time.monotonic()
    answer = crawler.default_fetcher(BASE, 3_000_000, total_seconds=0.2)
    assert time.monotonic() - started < 2
    assert 0 < len(answer.body) < 10_000


def test_a_server_holding_the_headers_back_cannot_hold_the_worker(monkeypatch):
    release = threading.Event()

    def never_answers(*_a, **_k):
        release.wait(30)
        raise OSError("gone")

    monkeypatch.setattr(crawler, "open_public", never_answers)
    started = time.monotonic()
    try:
        answer = crawler.default_fetcher(BASE, 1000, timeout=0.1, total_seconds=0.1)
        assert answer.status is None
        assert time.monotonic() - started < 2
    finally:
        release.set()


def test_a_read_stops_at_its_limit(monkeypatch):
    monkeypatch.setattr(crawler, "open_public", lambda *a, **k: _Socket([b"x" * 1000] * 100))
    assert len(crawler.default_fetcher(BASE, 2500).body) <= 3000


@pytest.mark.parametrize("error", [InvalidURL("bad"), IncompleteRead(b""), BadStatusLine("x"), OSError("x"), ValueError("x")])
def test_any_failure_of_one_address_is_no_answer_and_not_a_failed_scan(monkeypatch, error):
    def fails(*_a, **_k):
        raise error

    monkeypatch.setattr(crawler, "open_public", fails)
    assert crawler.default_fetcher(BASE, 1000).status is None


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [
        ("https://shop.example/کفش مردانه", "https://shop.example/%DA%A9%D9%81%D8%B4%20%D9%85%D8%B1%D8%AF%D8%A7%D9%86%D9%87"),
        ("https://shop.example/foo bar?q=a b", "https://shop.example/foo%20bar?q=a%20b"),
        ("https://shop.example/%D8%A7?a=1&b=2", "https://shop.example/%D8%A7?a=1&b=2"),
        ("https://فروشگاه.example/", "https://" + "فروشگاه".encode("idna").decode("ascii") + ".example/"),
    ],
)
def test_addresses_go_out_encoded_and_are_not_encoded_twice(raw, encoded):
    assert crawler.encode_url(raw) == encoded


def test_a_persian_address_reaches_the_server_and_the_sitemap_encoded(monkeypatch):
    seen = []

    def records(request, **_k):
        seen.append(request.full_url)
        return _Socket([b"<title>x</title>"])

    monkeypatch.setattr(crawler, "open_public", records)
    crawler.default_fetcher("https://shop.example/کفش", 1000)
    assert seen == ["https://shop.example/%DA%A9%D9%81%D8%B4"]
    text = build_sitemap(["https://shop.example/کفش"])
    assert "https://shop.example/%DA%A9%D9%81%D8%B4" in text
    assert parse_sitemap(text.encode("utf-8"))[0] == ["https://shop.example/%DA%A9%D9%81%D8%B4"]


def test_a_link_nobody_can_parse_is_passed_over():
    site = FakeSite({"/": html(links=["http://[x", "/ok"]), "/ok": html("صفحهٔ سالم با عنوانی کاملاً کافی")})
    assert len(crawl(site).pages) == 2


def test_markup_that_breaks_the_parser_costs_only_what_came_after_it(monkeypatch):
    def breaks(self, data):
        self.title = "عنوان خوانده‌شده پیش از خرابی"
        raise AssertionError("bad markup")

    monkeypatch.setattr(crawler._PageParser, "feed", breaks)
    assert crawl(FakeSite({"/": html()})).pages[0].title == "عنوان خوانده‌شده پیش از خرابی"


def test_a_page_field_is_cut_to_size_and_cleaned_for_the_database():
    page_text = html("عنوان\x00 " + "ب" * 5000)
    title = crawl(FakeSite({"/": page_text})).pages[0].title
    assert len(title) <= crawler.MAX_FIELD_CHARACTERS
    assert "\x00" not in title


def test_a_page_that_did_not_answer_is_not_called_broken_or_dropped_from_the_sitemap():
    site = FakeSite(
        {"/": html(links=["/new"]), "/new": html("صفحهٔ تازه با عنوانی کاملاً کافی"), "/busy": Fetched(status=503)},
        sitemaps={"/sitemap.xml": urlset("/", "/busy")},
    )
    world = World(site)
    scan = world.scan()
    assert "page_no_answer" in {issue["code"] for issue in scan["issues"]}
    assert "sitemap_lists_broken_page" not in {issue["code"] for issue in scan["issues"]}
    assert world.files.find("site-1", SiteFileKind.SITEMAP) is None, "not on the evidence of a bad minute"


def test_a_scanner_kept_out_by_name_stays_out():
    site = FakeSite(
        {"/": html(links=["/a"]), "/a": html("صفحهٔ الف با عنوانی کاملاً کافی")},
        robots="User-agent: HoshyarSEO-Scan\nDisallow: /a\n",
    )
    result = crawl(site)
    assert f"{BASE}a" not in site.fetched
    assert [item.url for item in result.pages] == [BASE]


def test_a_crawl_delay_is_kept_up_to_a_limit():
    slept = []
    site = FakeSite({"/": html(links=["/a"]), "/a": html("صفحهٔ الف با عنوان کافی")}, robots="User-agent: *\nCrawl-delay: 3\n")
    SiteCrawler(fetcher=site, delay_seconds=0.1, sleep=slept.append).crawl(BASE)
    assert slept and slept[0] == 3
    slept.clear()
    site.robots = "User-agent: *\nCrawl-delay: 9999\n"
    SiteCrawler(fetcher=site, delay_seconds=0.1, sleep=slept.append).crawl(BASE)
    assert slept[0] == crawler.MAX_CRAWL_DELAY_SECONDS


def test_the_queue_has_a_limit(monkeypatch):
    monkeypatch.setattr(crawler, "MAX_QUEUE", 5)
    site = FakeSite({"/": html(links=[f"/p{i}" for i in range(50)])})
    result = crawl(site)
    assert result.pages_capped
    assert len(result.pages) <= 5


@pytest.mark.parametrize(
    "answer",
    [
        Fetched(status=200, body=b"<!-- x --><html>oops</html>", content_type="text/html"),
        Fetched(status=200, body=b"<br /><b>Warning</b>: php", content_type="text/plain"),
    ],
)
def test_a_page_at_the_robots_address_is_no_robots_file_and_gets_no_html_kept(answer):
    site = FakeSite({"/": html()})
    site.robots_answer = answer
    original = site.__call__

    def fetch(url, limit):
        return answer if url.endswith("/robots.txt") else original(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    assert result.robots_text is None
    text, _ = improve_robots(result.robots_text, sitemap_url=None)
    assert "<" not in text


def test_a_bom_from_a_windows_editor_does_not_hide_the_first_rule():
    answer = Fetched(status=200, body=b"\xef\xbb\xbfUser-agent: *\nDisallow: /\n", content_type="text/plain")
    site = FakeSite({"/": html()})

    def fetch(url, limit):
        return answer if url.endswith("/robots.txt") else site(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    assert "robots_blocks_everything" in codes(build_issues(result))


@pytest.mark.parametrize(
    "answer",
    [
        Fetched(status=200, body="User-agent: *\nDisallow: /کفش\n".encode("cp1256"), content_type="text/plain"),
        Fetched(status=200, body=b"User-agent: *\n" + b"# x\n" * 200_000, content_type="text/plain"),
        Fetched(status=200, body=b"User-agent: *\n\x00\n", content_type="text/plain"),
    ],
)
def test_a_robots_we_cannot_read_whole_gets_no_proposal(answer):
    site = FakeSite({"/": html()})

    def fetch(url, limit):
        return answer if url.endswith("/robots.txt") else site(url, limit)

    world = World(site)
    world.scan_handler = build_site_scan_handler(
        site_store=world.sites, scan_store=world.scans, file_store=world.files,
        crawler_factory=lambda: SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None),
    )
    world.client.post("/v1/sites/site-1/scans")
    world.scan_handler({"site_id": "site-1", "job_id": world.scans.find("site-1").job_id})
    assert world.files.find("site-1", SiteFileKind.ROBOTS) is None
    assert "robots_unreadable" in {issue.code for issue in world.scans.find("site-1").issues}


# Exact undo, however it got there.


# No state without a way out.


# A scan and what is already there.


def test_a_proposal_a_later_scan_no_longer_needs_cannot_be_approved():
    site = new_site()
    world = World(site)
    world.scan()
    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.PROPOSED

    # The customer fixed both by hand.
    site.sitemaps["/sitemap.xml"] = urlset("/", "/a")
    site.robots = "User-agent: *\nDisallow:\n\nSitemap: https://shop.example/sitemap.xml\n"
    world.now += timedelta(minutes=6)
    world.scan()

    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.NOT_NEEDED
    assert world.files.find("site-1", SiteFileKind.SITEMAP).status == SiteFileStatus.NOT_NEEDED
    assert world.client.post("/v1/sites/site-1/site-files/robots/approve").status_code == 409


def test_a_scan_that_could_not_see_the_site_changes_no_proposal():
    site = new_site()
    world = World(site)
    world.scan()
    site.pages["/"] = Fetched(status=None)
    world.now += timedelta(minutes=6)
    world.scan()
    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.PROPOSED


# Limits.


def add_site(world, site_id, base=BASE, tenant="principal-1"):
    world.sites.create(Site(site_id=site_id, principal_id=tenant, name="n", base_url=base))


def test_several_records_for_one_address_do_not_get_around_the_wait():
    world = World(new_site())
    add_site(world, "site-2", "https://www.shop.example/")
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202
    world.run_jobs()
    assert world.client.post("/v1/sites/site-2/scans").status_code == 429


def test_one_scan_at_a_time_for_a_tenant():
    world = World(new_site())
    add_site(world, "site-2", "https://other.example/")
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202
    assert world.client.post("/v1/sites/site-2/scans").status_code == 409


def test_a_tenant_has_a_daily_limit_of_sites():
    world = World(new_site())
    for number in range(SCANS_PER_TENANT_PER_DAY):
        add_site(world, f"site-x{number}", f"https://other{number}.example/")
        assert world.client.post(f"/v1/sites/site-x{number}/scans").status_code == 202
        world.scans.save(world.scans.find(f"site-x{number}").model_copy(update={"status": ScanStatus.COMPLETED}))
    assert world.client.post("/v1/sites/site-1/scans").status_code == 429
    world.now += timedelta(days=1, minutes=1)
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202


def test_a_failed_scan_waits_like_any_other():
    world = World(new_site())
    world.client.post("/v1/sites/site-1/scans")
    world.scans.save(world.scans.find("site-1").model_copy(update={"status": ScanStatus.FAILED}))
    assert world.client.post("/v1/sites/site-1/scans").status_code == 429


def test_a_port_other_than_the_web_ones_is_not_scanned():
    world = World(new_site())
    add_site(world, "site-2", "https://shop.example:8443/")
    assert world.client.post("/v1/sites/site-2/scans").status_code == 422


def test_checking_again_and_again_is_slowed():
    world = World(new_site())
    world.scan()
    world.post("robots", "approve", expect=200)
    world.post("robots", "check")
    # The history is stamped by the real clock, so that is what the wait is against.
    world.now = datetime.now(timezone.utc)
    assert world.client.post("/v1/sites/site-1/site-files/robots/check").status_code == 429
    world.now += timedelta(seconds=31)
    assert world.client.post("/v1/sites/site-1/site-files/robots/check").status_code == 202


def test_the_file_is_served_so_it_can_only_be_a_download():
    world = World(new_site())
    world.scan()
    response = world.client.get("/v1/sites/site-1/site-files/robots/content")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["content-disposition"].startswith("attachment")


# The last round's review.


def test_a_file_put_in_place_that_later_went_missing_is_proposed_again():
    site = new_site()
    world = World(site)
    world.scan()
    change = world.post("robots", "approve", expect=200)
    site.robots = change.proposed_text
    assert world.post("robots", "check").status == SiteFileStatus.APPLIED

    site.robots = None  # a restore of the host, say
    world.now += timedelta(minutes=6)
    world.scan()

    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.PROPOSED


def test_a_passing_silence_does_not_take_an_approved_sitemap_from_the_customer():
    site = FakeSite(
        {"/": html(links=["/new"]), "/new": html("صفحهٔ تازه با عنوانی کاملاً کافی")},
        sitemaps={"/sitemap.xml": urlset("/", "/gone")},
    )
    world = World(site)
    world.scan()
    world.post("sitemap", "approve", expect=200)

    site.pages["/new"] = Fetched(status=503, final_url=f"{BASE}new")
    world.now += timedelta(minutes=6)
    world.scan()

    assert world.files.find("site-1", SiteFileKind.SITEMAP).status == SiteFileStatus.WAITING_FOR_CUSTOMER


@pytest.mark.parametrize("status_code", [None, 403, 503])
def test_a_sitemap_address_that_would_not_say_is_not_no_sitemap(status_code):
    site = FakeSite({"/": html()})

    def fetch(url, limit):
        return Fetched(status=status_code) if url.endswith("/sitemap.xml") else site(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    issues = build_issues(result)
    assert "no_sitemap" not in codes(issues)
    assert "sitemap_unreadable" in codes(issues)
    assert plan_site_files(base_url=BASE, result=result, issues=issues)[0].kind == SiteFileKind.ROBOTS
    assert all(item.kind != SiteFileKind.SITEMAP for item in plan_site_files(base_url=BASE, result=result, issues=issues))


def test_a_silent_guess_does_not_hide_a_sitemap_found_at_the_next_address():
    site = FakeSite({"/": html()}, sitemaps={"/sitemap_index.xml": urlset("/")})

    def fetch(url, limit):
        return Fetched(status=None) if url.endswith("/sitemap.xml") else site(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    assert result.sitemap_urls and not result.sitemap_uncertain


def test_a_robots_cut_short_is_not_taken_for_the_whole_file():
    site = FakeSite({"/": html()})
    answer = Fetched(status=200, body=b"User-agent: *\nDisallow: /", complete=False, content_type="text/plain")

    def fetch(url, limit):
        return answer if url.endswith("/robots.txt") else site(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    assert result.robots_text is None
    assert result.robots_unreadable
    assert "robots_blocks_everything" not in codes(build_issues(result))
    assert all(item.kind != SiteFileKind.ROBOTS for item in plan_site_files(base_url=BASE, result=result, issues=build_issues(result)))


def test_a_connection_dropped_mid_body_is_noticed(monkeypatch):
    class Cut(_Socket):
        def __init__(self):
            super().__init__([b"User-agent: *\nDisallow: /"])
            self.headers = {"Content-Type": "text/plain", "Content-Length": "500"}

    monkeypatch.setattr(crawler, "open_public", lambda *a, **k: Cut())
    assert crawler.default_fetcher(BASE, 1000).complete is False


def test_a_whole_answer_is_whole(monkeypatch):
    class Whole(_Socket):
        def __init__(self):
            super().__init__([b"abc"])
            self.headers = {"Content-Type": "text/plain", "Content-Length": "3"}

    monkeypatch.setattr(crawler, "open_public", lambda *a, **k: Whole())
    assert crawler.default_fetcher(BASE, 1000).complete is True


def test_a_mark_on_the_first_line_of_a_real_robots_is_not_called_no_file():
    site = FakeSite({"/": html()}, robots="# robots for <site>\nUser-agent: *\nDisallow: /x/\n")
    result = crawl(site)
    assert result.robots_unreadable
    assert "no_robots" not in codes(build_issues(result))
    assert all(item.kind != SiteFileKind.ROBOTS for item in plan_site_files(base_url=BASE, result=result, issues=build_issues(result)))


def test_a_link_to_another_port_is_not_this_site():
    site = FakeSite({"/": html(links=["https://shop.example:25/x", "/ok"]), "/ok": html("صفحهٔ سالم با عنوانی کاملاً کافی")})
    result = crawl(site)
    assert not any(":25" in url for url in site.fetched)
    assert len(result.pages) == 2


def test_a_nul_in_a_link_is_passed_over():
    site = FakeSite({"/": html(links=["/a\x00b", "/ok"]), "/ok": html("صفحهٔ سالم با عنوانی کاملاً کافی")})
    assert all("\x00" not in item.url for item in crawl(site).pages)


def test_a_queued_scan_of_a_removed_site_does_not_count_against_the_next_one():
    world = World(new_site())
    world.client.post("/v1/sites/site-1/scans")
    assert world.client.delete("/v1/sites/site-1").status_code == 204
    world.run_jobs()
    add_site(world, "site-2", "https://other.example/")
    assert world.client.post("/v1/sites/site-2/scans").status_code == 202


def test_there_is_a_limit_on_threads_left_behind(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(crawler, "_FETCH_SLOTS", slots)
    assert slots.acquire(blocking=False)
    called = []
    monkeypatch.setattr(crawler, "open_public", lambda *a, **k: called.append(1))
    assert crawler.default_fetcher(BASE, 1000).status is None
    assert called == []
    slots.release()


def test_a_queued_scan_of_a_removed_site_does_nothing():
    site = new_site()
    world = World(site)
    world.client.post("/v1/sites/site-1/scans")
    assert world.client.delete("/v1/sites/site-1").status_code == 204
    site.fetched.clear()
    world.run_jobs()
    assert site.fetched == []


# Manual only: we make the file, the customer puts it up.


def test_a_wordpress_site_gets_the_file_to_put_up_too_and_nothing_is_written():
    world = World(new_site(), adapter_type="hoshyarseo")
    world.scan()
    sitemap = world.files.find("site-1", SiteFileKind.SITEMAP)
    assert sitemap.public_url == f"{BASE}sitemap.xml"
    assert world.post("sitemap", "approve", expect=200).status == SiteFileStatus.WAITING_FOR_CUSTOMER


@pytest.mark.parametrize(
    ("status_value", "verb"),
    [
        (SiteFileStatus.APPLIED, "approve"),
        (SiteFileStatus.APPLIED, "check"),
        (SiteFileStatus.APPLIED, "reject"),
        (SiteFileStatus.PROPOSED, "check"),
        (SiteFileStatus.NOT_NEEDED, "approve"),
        (SiteFileStatus.REJECTED, "reject"),
    ],
)
def test_a_step_that_makes_no_sense_now_is_refused(status_value, verb):
    world = World(new_site())
    world.scan()
    current = world.files.find("site-1", SiteFileKind.ROBOTS)
    world.files.save(current.model_copy(update={"status": status_value}))
    assert world.client.post(f"/v1/sites/site-1/site-files/robots/{verb}").status_code == 409


def test_there_is_no_way_to_write_or_undo_a_file_from_here():
    world = World(new_site())
    world.scan()
    assert world.client.post("/v1/sites/site-1/site-files/robots/rollback").status_code in {404, 405}


def test_an_old_sitemap_left_in_place_does_not_count_as_the_new_one():
    site = FakeSite(
        {"/": html(links=["/new"]), "/new": html("صفحهٔ تازه با عنوانی کاملاً کافی")},
        sitemaps={"/sitemap.xml": urlset("/", "/gone")},
    )
    world = World(site)
    world.scan()
    assert world.post("sitemap", "approve", expect=200).status == SiteFileStatus.WAITING_FOR_CUSTOMER

    assert world.post("sitemap", "check").status == SiteFileStatus.WAITING_FOR_CUSTOMER, "the old file is still there"
    assert world.files.find("site-1", SiteFileKind.SITEMAP).note == "different_content"

    site.sitemaps["/sitemap.xml"] = urlset("/", "/new")
    assert world.post("sitemap", "check").status == SiteFileStatus.APPLIED


def test_a_stale_sitemap_line_in_robots_does_not_hide_the_real_sitemap():
    site = FakeSite(
        {"/": html()},
        robots="User-agent: *\nDisallow:\n\nSitemap: https://shop.example/old-sitemap.xml\n",
        sitemaps={"/wp-sitemap.xml": urlset("/")},
    )
    result = crawl(site)
    assert result.sitemap_urls == ["https://shop.example/wp-sitemap.xml"]
    assert "no_sitemap" not in codes(build_issues(result))


def test_a_silent_part_of_a_found_sitemap_keeps_it_uncertain():
    index = (
        b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://shop.example/a.xml</loc></sitemap><sitemap><loc>https://shop.example/b.xml</loc></sitemap></sitemapindex>"
    )
    site = FakeSite({"/": html(links=["/x"]), "/x": html("صفحهٔ ایکس با عنوانی کاملاً کافی")}, sitemaps={"/sitemap.xml": index, "/a.xml": urlset("/")})

    def fetch(url, limit):
        return Fetched(status=503) if url.endswith("/b.xml") else site(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    assert result.sitemap_urls and result.sitemap_uncertain
    assert all(plan.kind != SiteFileKind.SITEMAP for plan in plan_site_files(base_url=BASE, result=result, issues=build_issues(result)))


def test_a_page_cut_off_before_its_end_is_no_answer_not_a_page_without_a_title():
    site = FakeSite({"/": html(links=["/slow"]), "/slow": Fetched(status=200, body=b"<html><head>", complete=False, content_type="text/html", final_url=f"{BASE}slow")})
    issues = build_issues(crawl(site))
    assert "missing_title" not in codes(issues, f"{BASE}slow")
    assert "page_no_answer" in codes(issues, f"{BASE}slow")


def test_a_real_robots_file_served_as_html_is_still_a_file():
    answer = Fetched(status=200, body=b"User-agent: *\nDisallow: /private/\n", content_type="text/html")
    site = FakeSite({"/": html()})

    def fetch(url, limit):
        return answer if url.endswith("/robots.txt") else site(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    assert result.robots_text == "User-agent: *\nDisallow: /private/\n"


def test_a_robots_never_names_a_sitemap_the_customer_said_no_to():
    world = World(new_site())
    world.scan()
    world.post("sitemap", "reject", expect=200)
    world.now += timedelta(minutes=6)
    world.scan()
    robots = world.files.find("site-1", SiteFileKind.ROBOTS)
    assert "Sitemap:" not in robots.proposed_text
    assert world.files.find("site-1", SiteFileKind.SITEMAP).status == SiteFileStatus.REJECTED


def test_a_refused_sitemap_is_not_asked_again_because_a_page_was_added():
    site = new_site()
    world = World(site)
    world.scan()
    world.post("sitemap", "reject", expect=200)
    site.pages["/"] = html(links=["/a", "/b"])
    site.pages["/b"] = html("صفحهٔ ب با عنوانی کاملاً کافی")
    world.now += timedelta(minutes=6)
    world.scan()
    assert world.files.find("site-1", SiteFileKind.SITEMAP).status == SiteFileStatus.REJECTED


def test_only_the_start_of_a_huge_page_is_parsed(monkeypatch):
    fed = []
    original = crawler._PageParser.feed

    def feed(self, data):
        fed.append(len(data))
        return original(self, data)

    monkeypatch.setattr(crawler._PageParser, "feed", feed)
    crawl(FakeSite({"/": html(body="کلمه " * 400_000)}))
    assert fed and max(fed) <= crawler.MAX_PARSE_BYTES


def test_a_check_for_a_removed_site_looks_at_nothing():
    site = new_site()
    world = World(site)
    world.scan()
    world.post("robots", "approve", expect=200)
    world.now += timedelta(seconds=31)
    world.client.post("/v1/sites/site-1/site-files/robots/check")
    assert world.client.delete("/v1/sites/site-1").status_code == 204
    site.fetched.clear()
    world.run_jobs()
    assert site.fetched == []


# The review of the manual stage.


@pytest.mark.parametrize(
    "text",
    [
        "User-agent: Googlebot\nAllow: /\n\nUser-agent: *\nDisallow: /\n",
        "User-agent: *\nDisallow: /\nAllow: /blog/\n",
        "User-agent: Googlebot\nDisallow:\n\nUser-agent: *\nDisallow: /\n",
    ],
)
def test_a_site_open_to_google_is_not_called_closed_and_is_not_opened_to_everyone(text):
    assert not robots_blocks_everything(text)
    proposed, changes = improve_robots(text, sitemap_url=None)
    assert "removed_block_all" not in changes
    assert proposed == text


def test_only_the_group_google_obeys_is_opened():
    text = "User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /\n"
    assert robots_blocks_everything(text)
    proposed, changes = improve_robots(text, sitemap_url=None)
    assert changes == ["removed_block_all"]
    assert proposed == "User-agent: Googlebot\nDisallow:\n\nUser-agent: *\nDisallow: /\n", "other robots stay out"


def test_a_refused_robots_built_on_an_old_file_goes_away_when_the_customer_fixes_it():
    site = FakeSite({"/": html()}, robots="User-agent: *\nDisallow: /cart/\n")
    world = World(site)
    world.scan()
    world.post("robots", "reject", expect=200)

    site.robots = "User-agent: *\nDisallow: /cart/\nDisallow: /checkout/\n\nSitemap: https://shop.example/sitemap.xml\n"
    site.sitemaps["/sitemap.xml"] = urlset("/")
    world.now += timedelta(minutes=6)
    world.scan()

    assert world.files.find("site-1", SiteFileKind.ROBOTS).status == SiteFileStatus.NOT_NEEDED
    assert world.client.post("/v1/sites/site-1/site-files/robots/approve").status_code == 409


def test_a_refused_robots_is_rebuilt_from_the_file_as_it_is_now():
    site = FakeSite({"/": html()}, robots="User-agent: *\nDisallow: /cart/\n")
    world = World(site)
    world.scan()
    world.post("robots", "reject", expect=200)

    site.robots = "User-agent: *\nDisallow: /cart/\nDisallow: /checkout/\n"
    world.now += timedelta(minutes=6)
    world.scan()

    change = world.files.find("site-1", SiteFileKind.ROBOTS)
    assert change.status == SiteFileStatus.PROPOSED
    assert "Disallow: /checkout/" in change.proposed_text


def test_a_standing_sitemap_proposal_keeps_its_list_current():
    site = FakeSite(
        {"/": html(links=["/new"]), "/new": html("صفحهٔ تازه با عنوانی کاملاً کافی")},
        sitemaps={"/sitemap.xml": urlset("/", "/gone")},
    )
    world = World(site)
    world.scan()
    site.pages["/"] = html(links=["/new", "/newer"])
    site.pages["/newer"] = html("صفحهٔ تازه‌تر با عنوانی کاملاً کافی")
    world.now += timedelta(minutes=6)
    world.scan()

    change = world.files.find("site-1", SiteFileKind.SITEMAP)
    assert change.status == SiteFileStatus.PROPOSED
    assert f"{BASE}newer" in change.proposed_text


def test_a_sitemap_the_customer_has_but_that_is_now_short_is_proposed_again():
    site = FakeSite(
        {"/": html(links=["/new"]), "/new": html("صفحهٔ تازه با عنوانی کاملاً کافی")},
        sitemaps={"/sitemap.xml": urlset("/", "/gone")},
    )
    world = World(site)
    world.scan()
    world.post("sitemap", "approve", expect=200)
    site.pages["/"] = html(links=["/new", "/newer"])
    site.pages["/newer"] = html("صفحهٔ تازه‌تر با عنوانی کاملاً کافی")
    world.now += timedelta(minutes=6)
    world.scan()

    change = world.files.find("site-1", SiteFileKind.SITEMAP)
    assert change.status == SiteFileStatus.PROPOSED
    assert f"{BASE}newer" in change.proposed_text


def test_an_improved_sitemap_keeps_what_the_scan_has_no_case_against():
    site = FakeSite(
        {
            "/": html(),
            "/notitle": b"<html><body><p>no title here</p></body></html>",
            "/doc.pdf": Fetched(status=200, body=b"%PDF", content_type="application/pdf"),
        },
        sitemaps={"/sitemap.xml": urlset("/", "/notitle", "/doc.pdf", "/gone")},
    )
    world = World(site)
    world.scan()
    change = world.files.find("site-1", SiteFileKind.SITEMAP)
    assert change.changes == ["improved"]
    listed = parse_sitemap(change.proposed_text.encode("utf-8"))[0]
    assert f"{BASE}notitle" in listed and f"{BASE}doc.pdf" in listed
    assert f"{BASE}gone" not in listed
    assert change.dropped == [f"{BASE}gone"]


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_a_refusal_mid_scan_is_no_answer_not_a_broken_page(status_code):
    site = FakeSite(
        {"/": html(links=["/x"]), "/x": Fetched(status=status_code, final_url=f"{BASE}x")},
        sitemaps={"/sitemap.xml": urlset("/", "/x")},
    )
    world = World(site)
    scan = world.scan()
    codes_found = {issue["code"] for issue in scan["issues"]}
    assert "page_no_answer" in codes_found and "sitemap_lists_broken_page" not in codes_found
    assert world.files.find("site-1", SiteFileKind.SITEMAP) is None


def test_an_improved_sitemap_counts_as_in_place_only_once_the_dropped_pages_are_gone():
    site = FakeSite({"/": html()}, sitemaps={"/sitemap.xml": urlset("/", "/gone")})
    world = World(site)
    world.scan()
    world.post("sitemap", "approve", expect=200)

    assert world.post("sitemap", "check").status == SiteFileStatus.WAITING_FOR_CUSTOMER, "the old file is untouched"

    site.sitemaps["/sitemap.xml"] = urlset("/")
    assert world.post("sitemap", "check").status == SiteFileStatus.APPLIED


def test_images_listed_in_a_sitemap_are_not_pages():
    body = (
        b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        b'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"><url><loc>https://shop.example/a</loc>'
        b"<image:image><image:loc>https://shop.example/a.jpg</image:loc></image:image></url></urlset>"
    )
    assert parse_sitemap(body)[0] == ["https://shop.example/a"]


def test_a_sitemap_named_on_another_host_is_unknown_not_missing():
    site = FakeSite({"/": html()}, robots="User-agent: *\nDisallow:\n\nSitemap: https://cdn.other.example/map.xml\n")
    result = crawl(site)
    issues = build_issues(result)
    assert "no_sitemap" not in codes(issues)
    assert all(plan.kind != SiteFileKind.SITEMAP for plan in plan_site_files(base_url=BASE, result=result, issues=issues))


def test_no_sitemap_is_proposed_when_robots_could_not_be_read():
    site = FakeSite({"/": html()}, robots_status=503)
    result = crawl(site)
    assert plan_site_files(base_url=BASE, result=result, issues=build_issues(result)) == []


def test_a_cache_comment_on_top_of_real_rules_is_not_called_no_file():
    answer = Fetched(status=200, body=b"<!-- cached -->\nUser-agent: *\nDisallow: /x/\n", content_type="text/plain")
    site = FakeSite({"/": html()})

    def fetch(url, limit):
        return answer if url.endswith("/robots.txt") else site(url, limit)

    result = SiteCrawler(fetcher=fetch, delay_seconds=0, sleep=lambda _: None).crawl(BASE)
    assert result.robots_unreadable
    assert all(plan.kind != SiteFileKind.ROBOTS for plan in plan_site_files(base_url=BASE, result=result, issues=build_issues(result)))


# The second review of the manual stage.


@pytest.mark.parametrize(
    ("lines", "path", "allowed"),
    [
        ("User-agent: *\nDisallow: /shop/\nAllow: /shop/products/\n", "/shop/products/a", True),
        ("User-agent: *\nDisallow: /shop/\nAllow: /shop/products/\n", "/shop/cart", False),
        ("User-agent: *\nDisallow: /*.pdf$\n", "/files/a.pdf", False),
        ("User-agent: *\nDisallow: /*.pdf$\n", "/files/a.pdf?x=1", True),
        ("User-agent: *\nDisallow: /*?\n", "/a?b=1", False),
        ("User-agent: Googlebot/2.1\nDisallow: /private/\n\nUser-agent: *\nDisallow:\n", "/private/a", False),
        ("User-agent: *\nAllow: /a\nDisallow: /a\n", "/a", True),
    ],
)
def test_robots_is_read_the_way_google_reads_it(lines, path, allowed):
    assert google_allows(lines.splitlines(), f"https://shop.example{path}") is allowed


def test_a_page_google_may_read_is_not_dropped_for_a_first_match_reading():
    site = FakeSite(
        {"/": html(links=["/shop/products/a"]), "/shop/products/a": html("محصول الف با عنوانی کاملاً کافی")},
        robots="User-agent: *\nDisallow: /shop/\nAllow: /shop/products/\n",
        sitemaps={"/sitemap.xml": urlset("/", "/shop/products/a", "/gone")},
    )
    result = crawl(site)
    page = next(page for page in result.pages if page.url.endswith("/shop/products/a"))
    assert not page.robots_blocked
    kept, dropped, _ = improved_list(result)
    assert f"{BASE}shop/products/a" in kept and dropped == [f"{BASE}gone"]


def test_a_named_sitemap_not_read_leaves_a_guessed_one_uncertain():
    site = FakeSite(
        {"/": html()},
        robots="User-agent: *\nDisallow:\n\nSitemap: https://cdn.other.example/map.xml\n",
        sitemaps={"/sitemap.xml": urlset("/", "/gone")},
    )
    result = crawl(site)
    assert result.sitemap_urls and result.sitemap_uncertain
    assert all(plan.kind != SiteFileKind.SITEMAP for plan in plan_site_files(base_url=BASE, result=result, issues=build_issues(result)))


def test_a_refused_improvement_stays_refused_scan_after_scan():
    site = FakeSite({"/": html()}, sitemaps={"/sitemap.xml": urlset("/", "/gone")})
    world = World(site)
    world.scan()
    world.post("sitemap", "reject", expect=200)
    for _ in range(3):
        world.now += timedelta(minutes=6)
        world.scan()
        assert world.files.find("site-1", SiteFileKind.SITEMAP).status == SiteFileStatus.REJECTED


@pytest.mark.parametrize(
    "body",
    [
        b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://shop.example/pages.xml</loc></sitemap></sitemapindex>",
        b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        b'<url><loc>https://shop.example/</loc><xhtml:link rel="alternate" hreflang="en" href="https://shop.example/en/"/></url>'
        b"<url><loc>https://shop.example/gone</loc></url></urlset>",
    ],
)
def test_a_sitemap_with_more_than_addresses_is_not_replaced_by_a_plain_list(body):
    site = FakeSite({"/": html()}, sitemaps={"/sitemap.xml": body, "/pages.xml": urlset("/", "/gone")})
    result = crawl(site)
    issues = build_issues(result)
    assert result.sitemap_rich and "sitemap_lists_broken_page" in codes(issues)
    assert all(plan.kind != SiteFileKind.SITEMAP for plan in plan_site_files(base_url=BASE, result=result, issues=issues))


def test_the_health_score_counts_kinds_of_problem_not_pages():
    from app.models.site_scan import IssueSeverity, ScanIssue
    from app.scan.score import health_score

    pages = [ScannedPage(url=f"{BASE}{n}", status_code=200) for n in range(10)]
    critical = lambda code, url=None: ScanIssue(code=code, severity=IssueSeverity.CRITICAL, url=url)  # noqa: E731
    warning = lambda code, url=None: ScanIssue(code=code, severity=IssueSeverity.WARNING, url=url)  # noqa: E731
    assert health_score([], pages) == 100
    assert health_score([critical("missing_title", f"{BASE}0")], pages) == 94  # 15 x the 0.4 floor
    assert health_score([warning("no_sitemap")] * 3, pages) == 95, "one kind, counted once"
    assert health_score([ScanIssue(code="page_no_answer", severity=IssueSeverity.WARNING, url=f"{BASE}0")], pages) == 100


def test_a_site_google_cannot_see_never_looks_healthy():
    from app.models.site_scan import IssueSeverity, ScanIssue
    from app.scan.score import health_score

    pages = [ScannedPage(url=f"{BASE}{n}", status_code=200) for n in range(10)]
    assert health_score([ScanIssue(code="robots_blocks_everything", severity=IssueSeverity.CRITICAL, url=None)], pages) == 10
    everywhere = [ScanIssue(code="noindex_in_sitemap", severity=IssueSeverity.CRITICAL, url=page.url) for page in pages]
    assert health_score(everywhere, pages) == 40
    assert health_score([ScanIssue(code="home_unreachable", severity=IssueSeverity.CRITICAL, url=None)], []) is None


def test_a_finished_scan_carries_its_score():
    world = World(new_site(), adapter_type=None)
    scan = world.scan()
    codes_found = {issue["code"] for issue in scan["issues"]}
    assert {"no_sitemap", "no_robots"} <= codes_found
    assert scan["health_score"] < 100


def test_a_site_that_did_not_answer_has_no_score():
    world = World(FakeSite({"/": Fetched(status=None)}))
    assert world.scan()["health_score"] is None


# ---- speed, from the page's own answer and markup --------------------------------


def test_the_speed_signals_are_read_from_the_page_itself():
    head = (
        '<script src="/a.js"></script><script src="/b.js" defer></script><script src="/c.js" async></script>'
        '<script src="/d.js" type="module"></script><script src="/e.js"></script><script src="/f.js"></script>'
        '<link rel="stylesheet" href="/s.css">'
    )
    images = "".join(f'<img src="/p{n}.jpg">' for n in range(7)) + '<img src="/q.webp" width="10" height="10" loading="lazy">'
    site = FakeSite({"/": html(head=head, body="کلمه " * 150 + images)})
    page = crawl(site).pages[0]
    speed = page.speed
    assert (speed.blocking_scripts, speed.stylesheets, speed.images) == (3, 1, 8)
    assert (speed.images_unsized, speed.images_not_lazy, speed.images_old_format) == (7, 5, 7)
    codes_found = codes(build_issues(crawl(site)), url=page.url)
    assert {"render_blocking_scripts", "images_without_size", "images_not_lazy", "old_image_formats"} <= codes_found
    assert "slow_response" not in codes_found and "heavy_html" not in codes_found


def test_a_slow_site_is_judged_by_its_middle_page_not_one_page():
    from app.scan.issues import _slow_site

    def page(ms, n):
        return ScannedPage(url=f"{BASE}{n}", status_code=200, speed={"fetch_ms": ms, "html_kb": 20})

    assert _slow_site([page(900, 1), page(9000, 2), page(1200, 3)]) == []
    slow = _slow_site([page(3000, 1), page(2600, 2), page(900, 3)])
    assert [(issue.code, issue.url, issue.detail) for issue in slow] == [("slow_response", None, {"ms": 2600})]


def test_what_the_browser_does_not_load_is_not_counted():
    lazy_copy = '<img data-src="/a.jpg" class="lazyload"><noscript><img src="/a.jpg"></noscript>'
    pictures = (
        '<picture><source type="image/webp" srcset="/b.webp"><img src="/b.jpg" width="1" height="1"></picture>'
    )
    body = "کلمه " * 150 + lazy_copy * 6 + pictures * 6 + "<template><img src='/t.png'></template>"
    page = crawl(FakeSite({"/": html(body=body)})).pages[0]
    assert page.speed.images == 12, "the <noscript> and <template> copies are not more images"
    assert page.speed.images_not_lazy == 6, "data-src counts as lazy; the six pictures are not"
    assert page.speed.images_old_format == 6, "a <picture> with a WebP source is not an old format"


def test_a_page_without_head_or_body_tags_still_knows_where_its_body_begins():
    raw = (
        "<!doctype html><title>" + GOOD_TITLE + "</title><script src='/head.js'></script>"
        "<h1>x</h1>" + "کلمه " * 150 + "<script src='/1.js'></script><script src='/2.js'></script>"
        "<script src='/3.js'></script><script src='/rocket.js' type='rocketlazyloadscript'></script>"
    ).encode("utf-8")
    page = crawl(FakeSite({"/": raw})).pages[0]
    assert page.speed.blocking_scripts == 1


def test_heavy_html_and_the_time_taken_are_recorded():
    from app.scan.issues import speed_issues

    now = {"t": 0.0}
    site = FakeSite({"/": html(body="کلمه " * 110_000)})

    def slow_home(url, limit):
        if url == BASE:
            now["t"] += 3.25  # the page takes 3.25 seconds; nothing else does
        return site(url, limit)

    result = SiteCrawler(fetcher=slow_home, delay_seconds=0, sleep=lambda _: None, clock=lambda: now["t"]).crawl(BASE)
    page = result.pages[0]
    assert page.speed.fetch_ms == 3250
    assert page.speed.html_kb >= 500
    assert "heavy_html" in {issue.code for issue in speed_issues(page)}


# ---- the monthly scan ----------------------------------------------------------------


def monthly(world, **options):
    from app.scan.monthly import MonthlyRescan

    import uuid

    return MonthlyRescan(
        site_store=world.sites, scan_store=world.scans, job_store=world.jobs,
        job_id_factory=lambda: f"job-monthly-{uuid.uuid4().hex[:8]}", clock=lambda: world.now, **options,
    )


def test_a_site_is_scanned_again_a_month_after_its_last_scan_and_says_what_is_new():
    site = FakeSite({"/": html(links=["/a"]), "/a": html("صفحهٔ الف با عنوانی کاملاً کافی")})
    world = World(site)
    first = world.scan()
    assert first["new_issue_count"] is None, "the first scan has nothing to compare with"

    world.now += timedelta(days=10)
    assert monthly(world).run_round() == {"queued": 0, "due": 0}

    world.now += timedelta(days=21)
    site.pages["/b"] = html("")  # a page without a title appears
    site.pages["/"] = html(links=["/a", "/b"])
    assert monthly(world).run_round()["queued"] == 1
    world.run_jobs()
    again = world.client.get("/v1/sites/site-1/scans/latest").json()
    assert again["trigger"] == "monthly" and again["status"] == "completed"
    assert again["new_issue_count"] >= 1


def test_what_is_new_counts_what_the_summary_counts():
    from app.models.site_scan import IssueSeverity, ScanIssue
    from app.scan.handler import issue_keys_of

    issues = [
        ScanIssue(code="no_sitemap", severity=IssueSeverity.WARNING),
        ScanIssue(code="not_in_sitemap", severity=IssueSeverity.INFO, url=f"{BASE}a"),
    ]
    assert issue_keys_of(issues) == ["no_sitemap|"], "information is not a problem that is new"
    assert issue_keys_of([]) == []


def test_a_monthly_scan_on_its_way_or_failed_leaves_the_last_one_in_use():
    site = FakeSite({"/": html(links=["/a"]), "/a": html("صفحهٔ الف با عنوانی کاملاً کافی")})
    world = World(site)
    first = world.scan()
    world.now += timedelta(days=31)
    assert monthly(world).run_round()["queued"] == 1  # queued, not run yet

    shown = world.client.get("/v1/sites/site-1/scans/latest").json()
    assert shown["job_id"] == first["job_id"] and shown["status"] == "completed", "the customer is not made to wait"
    assert world.scans.completed("site-1").job_id == first["job_id"]

    site.pages["/"] = Fetched(status=None)  # the site is down when it runs
    site.pages["/a"] = Fetched(status=None)
    world.run_jobs()
    assert world.client.get("/v1/sites/site-1/scans/latest").json()["job_id"] == first["job_id"]
    assert monthly(world).run_round()["queued"] == 0, "a failed monthly scan waits a day"
    world.now += timedelta(days=1, minutes=1)
    assert monthly(world).run_round()["queued"] == 1


def test_a_monthly_round_never_takes_a_customers_turn():
    from app.models.sites import Site

    world = World(new_site())
    world.scan()
    world.sites.create(Site(site_id="site-2", principal_id="principal-1", name="n", base_url="https://two.example/"))
    world.scans.save(SiteScan(
        site_id="site-2", tenant_id="principal-1", job_id="old-2", status=ScanStatus.COMPLETED,
        requested_at=world.now, completed_at=world.now,
    ))
    world.now += timedelta(days=31)
    assert monthly(world).run_round()["queued"] == 1, "one per tenant a round"
    assert monthly(world).run_round()["queued"] == 0, "not beside the tenant's scan on its way"


def test_no_monthly_scan_for_a_site_never_scanned_removed_or_lapsed():
    from app.models.sites import SiteStatus

    world = World(new_site())
    assert monthly(world).run_round() == {"queued": 0, "due": 0}, "the customer starts the first one"
    world.scan()
    world.now += timedelta(days=31)
    assert monthly(world, active=lambda tenant: False).run_round()["queued"] == 0
    site = world.sites.get("site-1")
    world.sites.update(site.model_copy(update={"status": SiteStatus.REMOVED}))
    assert monthly(world).run_round()["queued"] == 0


def test_a_round_queues_a_few_the_longest_waiting_first():
    from app.models.sites import Site

    world = World(new_site())
    for n in range(2, 10):
        world.sites.create(Site(site_id=f"site-{n}", principal_id=f"tenant-{n}", name="n", base_url=f"https://s{n}.example/"))
        world.scans.save(SiteScan(
            site_id=f"site-{n}", tenant_id=f"tenant-{n}", job_id=f"old-{n}", status=ScanStatus.COMPLETED,
            requested_at=world.now - timedelta(days=40), completed_at=world.now - timedelta(days=40 - n),
        ))
    result = monthly(world, per_round=3).run_round()
    assert result == {"queued": 3, "due": 8}
    queued = [world.scans.find(f"site-{n}").trigger for n in range(2, 10)]
    assert queued[:3] == ["monthly"] * 3 and set(queued[3:]) == {"customer"}


def test_a_site_scanned_before_the_split_keeps_its_last_finished_scan():
    from app.scan.store import COMPLETED_SCAN_AGGREGATE_TYPE

    world = World(new_site())
    first = world.scan()
    # As a record from before: only the latest attempt was kept.
    record = world.repository.get(aggregate_type=COMPLETED_SCAN_AGGREGATE_TYPE, aggregate_id="site-1")
    world.repository._records.pop((COMPLETED_SCAN_AGGREGATE_TYPE, "site-1"))
    assert record is not None and world.scans.completed("site-1").job_id == first["job_id"]

    world.now += timedelta(days=31)
    assert monthly(world).run_round()["queued"] == 1
    assert world.scans.completed("site-1").job_id == first["job_id"], "kept when the new attempt was written"
    assert world.client.get("/v1/sites/site-1/scans/latest").json()["job_id"] == first["job_id"]


def test_the_customers_button_takes_the_place_of_a_monthly_scan():
    world = World(new_site())
    world.scan()
    world.now += timedelta(days=31)
    monthly(world).run_round()
    response = world.client.post("/v1/sites/site-1/scans")
    assert response.status_code == 202, response.text
    world.run_jobs()
    latest = world.client.get("/v1/sites/site-1/scans/latest").json()
    assert latest["trigger"] == "customer" and latest["status"] == "completed"


def test_a_site_that_stays_down_is_tried_less_often_and_the_customer_is_told():
    site = FakeSite({"/": html(links=["/a"]), "/a": html("صفحهٔ الف با عنوانی کاملاً کافی")})
    world = World(site)
    world.scan()
    site.pages["/"] = Fetched(status=None)
    world.now += timedelta(days=31)
    for wait_days in (1, 2, 4):
        assert monthly(world).run_round()["queued"] == 1
        world.run_jobs()
        shown = world.client.get("/v1/sites/site-1/scans/latest").json()
        assert shown["last_attempt_error"] == "site_unreachable" and shown["status"] == "completed"
        world.now += timedelta(days=wait_days) - timedelta(minutes=1)
        assert monthly(world).run_round()["queued"] == 0
        world.now += timedelta(minutes=2)


@pytest.mark.parametrize("then", ["answers", "is_down", "breaks"])
def test_a_monthly_scan_replaced_while_it_reads_the_site_leaves_the_new_one_alone(then):
    from app.scan.monthly import STALE_AFTER

    class PressedMidway(FakeSite):
        """A monthly scan read so long it went stale; the customer presses the button."""

        pressed = False

        def __call__(self, url, limit):
            latest = world.scans.find("site-1")
            if not self.pressed and latest.trigger == "monthly" and latest.status == ScanStatus.RUNNING:
                self.pressed = True
                world.now += STALE_AFTER + timedelta(minutes=1)
                assert world.client.post("/v1/sites/site-1/scans").status_code == 202
                if then == "is_down":
                    return Fetched(status=None)
                if then == "breaks":
                    raise RuntimeError("the crawler broke")
            return super().__call__(url, limit)

    world = World(PressedMidway(new_site().pages))
    world.scan()
    world.now += timedelta(days=31)
    monthly(world).run_round()
    world.run_jobs()
    latest = world.scans.find("site-1")
    assert latest.trigger == "customer" and latest.status == ScanStatus.COMPLETED, (latest.trigger, latest.status, latest.error)


def test_a_monthly_scan_reading_the_site_still_holds_the_button_and_a_queued_one_gives_way():
    world = World(new_site())
    world.scan()
    world.now += timedelta(days=31)
    monthly(world).run_round()
    attempt = world.scans.find("site-1")
    world.scans.save(attempt.model_copy(update={"status": ScanStatus.RUNNING}))
    assert world.client.post("/v1/sites/site-1/scans").status_code == 409
    world.scans.save(attempt)  # queued again: it gives way
    assert world.client.get("/v1/sites/site-1/scans/latest").json()["last_attempt_error"] is None
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202


def test_monthly_scans_do_not_count_against_the_days_limit():
    world = World(new_site())
    # A tenant with many sites, each scanned by the monthly round within the hour.
    for i in range(SCANS_PER_TENANT_PER_DAY):
        world.sites.create(Site(site_id=f"site-x{i}", principal_id="principal-1", name="n", base_url=f"https://x{i}.example/"))
        world.scans.save(SiteScan(
            site_id=f"site-x{i}", tenant_id="principal-1", job_id=f"job-x{i}", host=f"x{i}.example",
            requested_at=world.now - timedelta(hours=1), status=ScanStatus.COMPLETED, trigger="monthly",
        ))
    assert world.client.post("/v1/sites/site-1/scans").status_code == 202


def test_monthly_retries_wait_at_most_eight_days():
    from app.scan.monthly import MonthlyRescan

    world = World(new_site())
    world.scan()
    world.now += timedelta(days=31)
    monthly(world).run_round()
    attempt = world.scans.find("site-1")
    world.scans.save(attempt.model_copy(update={"status": ScanStatus.FAILED, "error": "site_unreachable", "monthly_failures": 6}))
    rescan = monthly(world)
    site = world.sites.get("site-1")
    assert rescan.due(site, attempt.requested_at + timedelta(days=8) - timedelta(minutes=1)) is None
    assert rescan.due(site, attempt.requested_at + timedelta(days=8)) is not None


def test_a_lapsed_subscription_is_not_promised_a_monthly_scan():
    world = World(new_site())
    world.scan()
    assert world.client.get("/v1/sites/site-1/scans/latest").json()["monthly_rescan"] is True

    class Lapsed:
        def entitlement(self, tenant_id):
            from types import SimpleNamespace
            return SimpleNamespace(active=False)

    world.client.app.state.dependencies.billing = Lapsed()
    assert world.client.get("/v1/sites/site-1/scans/latest").json()["monthly_rescan"] is False
