"""The dashboard's static files: what they call exists, and what they must never do they don't."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.models.sites import SiteAdapterConnection
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from tests.unit.test_stage37_38_actions import API_KEY, make_site

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
JS_FILES = sorted(FRONTEND.rglob("*.js"))
TEXT_FILES = [*JS_FILES, *FRONTEND.rglob("*.css"), *FRONTEND.rglob("*.html")]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def client_for(principal="principal-1", sites=()):
    repository = InMemoryRepository()
    store = SiteStore(repository)
    for site in sites:
        store.create(site)
    app = create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator(API_KEY, principal_id=principal),
        site_store=store,
    ))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {API_KEY}"
    return client


# ---- the static files ------------------------------------------------------------------


def test_every_api_path_the_pages_call_exists():
    called = set()
    for path in JS_FILES:
        for found in re.findall(r"""[`"'](/v1/[^`"'?]*)""", read(path)):
            called.add(found)
        # ${base}/... in the report page.
        for found in re.findall(r"`\$\{base\}(/[^`?]*)`", read(path)):
            called.add("/v1/sites/${id}" + found)
        for found in re.findall(r"`\$\{siteUrl\}(/[^`?]*)`", read(path)):
            called.add("/v1/sites/${id}/connections/site-adapter" + found)

    assert len(called) > 15, "the search above stopped finding the calls"
    routes = [route.path for route in client_for().app.routes if hasattr(route, "path")]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", route) + "$") for route in routes]
    for path in called:
        # A template hole is one path segment; a trailing one is added by the caller.
        concrete = re.sub(r"\$\{[^}]+\}", "x", path)
        assert any(pattern.match(concrete) for pattern in patterns) or concrete.endswith("/x") or concrete.endswith("/"), path


def test_nothing_is_loaded_from_another_origin():
    # Google Fonts and friends can be slow or filtered from Iran, and the
    # content security policy would refuse them anyway.
    allowed = {"www.w3.org", "example.com"}  # the SVG namespace; placeholder text
    for path in TEXT_FILES:
        for host in re.findall(r"https?://([^/\s\"'`)]+)", read(path)):
            assert host in allowed, f"{path.name}: {host}"


@pytest.mark.parametrize("forbidden", ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("])
def test_text_from_the_api_is_never_parsed_as_html(forbidden):
    for path in JS_FILES:
        assert forbidden not in read(path), path.name


def test_every_module_import_resolves():
    for path in JS_FILES:
        for target in re.findall(r"""from\s+"(\.[^"]+)\"""", read(path)):
            assert (path.parent / target).resolve().is_file(), f"{path.name} imports {target}"


def test_the_page_needs_no_inline_script_or_style():
    # The policy in deploy/nginx-app.conf allows neither.
    page = read(FRONTEND / "index.html")
    assert re.search(r"<script(?![^>]*\bsrc=)", page) is None
    assert "<style" not in page and " style=" not in page
    policy = read(ROOT / "deploy" / "nginx-app.conf")
    assert "script-src 'self'" in policy and "style-src 'self'" in policy and "unsafe" not in policy


def test_the_font_is_ours_and_its_licence_comes_with_it():
    assert (FRONTEND / "fonts" / "Vazirmatn.woff2").stat().st_size > 10_000
    assert "SIL Open Font License" in read(FRONTEND / "fonts" / "OFL.txt")


def test_frontend_setup_recognises_both_ways_the_snippet_is_included():
    # app-setup.sh writes a glob, the setup script a literal path. Missing either
    # would include the snippet twice, and nginx refuses a duplicate location.
    setup = read(ROOT / "deploy" / "frontend-setup.sh")
    [pattern] = re.findall(r"grep -q '(snippets/hoshyarseo-app)' \"\$SITE\"", setup)
    assert pattern in "include /etc/nginx/snippets/hoshyarseo-app*.conf;"
    assert pattern in "include /etc/nginx/snippets/hoshyarseo-app.conf;"
    assert "snippets/hoshyarseo-app*.conf" in read(ROOT / "deploy" / "app-setup.sh")


def test_the_google_connection_pages_lead_back_to_the_dashboard():
    from app.api.oauth_pages import done_page, error_page

    for page in (done_page("a@example.com", "/app/"), error_page("no", "/app/")):
        assert 'href="/app/#/settings"' in page.body.decode("utf-8")
    assert 'href="/"' in done_page(None).body.decode("utf-8"), "without a dashboard, the site"


# ---- what the pages needed from the API --------------------------------------------------


class FakeTableReports:
    def __init__(self):
        self.seen = []

    def _report(self, site_id, start, end, **kwargs):
        from app.warehouse.models import TableReport

        self.seen.append(kwargs.get("contains"))
        return TableReport(site_id=site_id, start=start, end=end, sort="clicks", order="desc", rows=[])

    def queries(self, *, site_id, start, end, **kwargs):
        return self._report(site_id, start, end, **kwargs)

    pages = queries


def report_client():
    from app.models.sites import GSCConnectionConfig
    from app.onboarding.google_oauth import tenant_credential_ref

    site = make_site().model_copy(update={"gsc": GSCConnectionConfig(
        property_url="sc-domain:example.com", credential_ref=tenant_credential_ref("principal-1"),
        auth_mode="oauth_refresh_token",
    )})
    repository = InMemoryRepository()
    store = SiteStore(repository)
    store.create(site)
    reports = FakeTableReports()
    app = create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator(API_KEY, principal_id="principal-1"),
        site_store=store, warehouse_reports=reports,
    ))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {API_KEY}"
    return client, reports


def test_a_query_search_is_normalised_like_the_stored_queries():
    client, reports = report_client()
    assert client.get("/v1/sites/site-1/performance/queries", params={"contains": " كفش "}).status_code == 200
    assert reports.seen == ["کفش"], "an Arabic kaf still finds the Persian one"


@pytest.mark.parametrize("path", ["queries", "pages"])
def test_a_search_is_bounded_and_nothing_or_a_nul_means_no_search(path):
    client, reports = report_client()
    for contains in ("", "   ", "\x00"):
        assert client.get(f"/v1/sites/site-1/performance/{path}", params={"contains": contains}).status_code == 200
    client.get(f"/v1/sites/site-1/performance/{path}", params={"contains": "a" * 500})
    assert reports.seen[:3] == [None, None, None]
    assert len(reports.seen[3]) == 200


def test_sites_are_listed_for_their_own_tenant_only():
    mine = make_site()
    theirs = make_site().model_copy(update={"site_id": "site-2", "principal_id": "principal-2"})
    client = client_for(sites=[mine, theirs])

    response = client.get("/v1/sites")

    assert response.status_code == 200
    assert [site["site_id"] for site in response.json()] == ["site-1"]


def test_a_site_says_how_it_is_connected_and_never_with_what():
    manual = make_site(adapter_configured=False).model_copy(
        update={"site_adapter": SiteAdapterConnection(adapter_type="manual")}
    )
    client = client_for(sites=[manual])
    [site] = client.get("/v1/sites").json()
    assert site["site_adapter_type"] == "manual"

    wordpress = client_for(sites=[make_site()]).get("/v1/sites").json()[0]
    assert wordpress["site_adapter_type"] == "wordpress"
    assert "WP_USER" not in str(wordpress), "the credential's reference stays on the server"
