"""The dashboard against the real API, with invented data and nothing else running.

    python scripts/dev_dashboard.py     # http://localhost:58090/app/

No PostgreSQL, no Google, no customer's site: stores are in memory, the Search
Console history is generated, and the two sample sites answer from a dict. The
routes, schemas, sessions and CSRF checks are the real ones, and the sample
actions are taken through the real executor and measurement, so what the
pages show has the shape production sends. Everything is gone when it stops.

Sign in with dev@example.test / dev-password (local sample account only).
"""
from __future__ import annotations

import io
import math
import os
import re
import sys
import threading
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import Response  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.accounts.service import AuthService  # noqa: E402
from app.accounts.store import AccountStore, SessionStore  # noqa: E402
from app.action.approval import approve, propose_title_change  # noqa: E402
from app.action.executor import (  # noqa: E402
    EXECUTE_ACTION_JOB_TYPE,
    ROLLBACK_ACTION_JOB_TYPE,
    build_execute_action_handler,
    build_rollback_action_handler,
)
from app.action.planner import build_action  # noqa: E402
from app.action.store import ManagedActionStore  # noqa: E402
from app.api.app import APIDependencies, create_app  # noqa: E402
from app.api.auth import APIKeyAuthenticator  # noqa: E402
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore  # noqa: E402
from app.learning.store import FeedbackStore  # noqa: E402
from app.measurement.runner import MeasurementRunner  # noqa: E402
from app.models.execution import ExecutionCapability  # noqa: E402
from app.models.opportunities import OpportunityType  # noqa: E402
from app.models.site_adapter import AdapterOperation, AdapterOperationResult, SitePage  # noqa: E402
from app.models.sites import GSCConnectionConfig, SecretProvider, SecretRef, Site, SiteAdapterConnection  # noqa: E402
from app.models.snapshots import SnapshotMetadata  # noqa: E402
from app.models.strategies import Strategy, StrategyStatus, StrategyType  # noqa: E402
from app.models.tenants import Tenant  # noqa: E402
from app.onboarding.google_oauth import GoogleConnection, tenant_credential_ref  # noqa: E402
from app.onboarding.site_store import SiteStore  # noqa: E402
from app.persistence.memory import InMemoryRepository  # noqa: E402
from app.runs.handler import SEO_RUN_JOB_TYPE  # noqa: E402
from app.runs.store import RunStore  # noqa: E402
from app.scan.crawler import Fetched, SiteCrawler  # noqa: E402
from app.scan.handler import (  # noqa: E402
    SITE_FILE_JOB_TYPE,
    SITE_SCAN_JOB_TYPE,
    build_site_file_handler,
    build_site_scan_handler,
)
from app.scan.store import SiteFileStore, SiteScanStore  # noqa: E402
from app.tenancy.store import TenantStore  # noqa: E402
from app.warehouse.models import (  # noqa: E402
    MetricValues,
    PerformanceReport,
    SeriesPoint,
    SyncDayStatus,
    SyncStatus,
    TableReport,
    TableRow,
)
from app.warehouse.planner import sync_window  # noqa: E402

PORT = 58090
TENANT = "t-dev"
NOW = datetime.now(timezone.utc)

SHOP = "https://shop.example.com"
PAGES = {
    f"{SHOP}/men-shoes": ["خرید کفش مردانه", "کفش مردانه چرم", "کفش اسپرت مردانه", "قیمت کفش مردانه"],
    f"{SHOP}/women-bags": ["کیف زنانه", "خرید کیف زنانه چرم", "کیف دوشی زنانه"],
    f"{SHOP}/%D8%B1%D8%A7%D9%87%D9%86%D9%85%D8%A7": ["راهنمای انتخاب سایز کفش", "سایز کفش"],
    f"{SHOP}/": ["فروشگاه نمونه", "فروشگاه کفش"],
}


class DevRepository(InMemoryRepository):
    @contextmanager
    def transaction(self):
        before = (dict(self._records), dict(self._record_ids))
        try:
            yield
        except BaseException:
            self._records, self._record_ids = before
            raise


def strategy_for(url: str, query: str, site_id: str) -> Strategy:
    return Strategy(
        strategy_id=f"strategy-{abs(hash(query))}", opportunity_id="opportunity-1", site_id=site_id,
        normalized_url=url, normalized_query=query, opportunity_type=OpportunityType.VISIBILITY_GROWTH,
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION, status=StrategyStatus.RECOMMENDED,
        confidence_score=0.9, expected_impact_score=0.8, effort_score=0.2, risk_score=0.1, priority_score=0.9,
        reasons=["sample"], evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="dev", data_snapshot_id="dev", rule_version="rules-v1", config_version="dev", generated_at=NOW,
        ),
    )


def proposal(run: str, site_id: str, url: str, query: str, current: str, title: str, *others: str):
    return propose_title_change(
        action=build_action(strategy=strategy_for(url, query, site_id), action_id=f"candidate-{run}"),
        run_id=run, tenant_id=TENANT, proposal_id=f"proposal:{run}", proposed_title=title,
        current_title=current, provider_id="sample", alternatives=[title, *others],
    )


class SampleSite:
    """Titles in a dict, standing in for a customer's pages."""

    def __init__(self, *, manual: bool) -> None:
        self.manual_apply = manual
        self.exact_rollback = False
        self.adapter_id = "manual:dev" if manual else "hoshyarseo:dev"
        self.titles: dict[str, str] = {}
        self.writes = 0

    @property
    def capabilities(self):
        if self.manual_apply:
            return {ExecutionCapability.READ_PAGE}
        return {ExecutionCapability.READ_PAGE, ExecutionCapability.UPDATE_TITLE}

    def status(self):
        return {"plugin_version": "dev", "seo_plugins": [], "home_url": SHOP, "can_verify_ownership": True}

    def read_page(self, *, site_id, normalized_url):
        return SitePage(site_id=site_id, normalized_url=normalized_url, title=self.titles.get(normalized_url, "عنوان فعلی صفحه"))

    def update_title(self, *, site_id, normalized_url, new_title, idempotency_key):
        self.writes += 1
        previous = self.titles.get(normalized_url)
        self.titles[normalized_url] = new_title
        return AdapterOperationResult(
            success=True, operation=AdapterOperation.UPDATE_TITLE, site_id=site_id, normalized_url=normalized_url,
            change_id=f"dev:{self.writes}", previous_value=previous, new_value=new_title,
        )



def _sample_page(title: str, links: list[str]) -> bytes:
    anchors = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return (
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1>"
        f"<p>{'متن نمونهٔ صفحه ' * 40}</p>{anchors}</body></html>"
    ).encode("utf-8")


SAMPLE_PUBLIC = {
    "shop.example.com": {
        "/": _sample_page("فروشگاه نمونه", ["/men-shoes", "/women-bags", "/gone"]),
        "/men-shoes": _sample_page("کفش مردانه | فروشگاه نمونه", ["/"]),
        "/women-bags": _sample_page("کیف زنانه", ["/"]),
    },
    "next.example.com": {
        "/": _sample_page("سایت Next.js نمونه با عنوانی که کافی است", ["/tennis-court", "/lessons"]),
        "/tennis-court": _sample_page("زمین تنیس", ["/"]),
        "/lessons": _sample_page("آموزش تنیس", ["/"]),
    },
}


def sample_fetcher():
    """The two sample sites from outside: the shop has a robots.txt, the other nothing."""

    def fetch(url, limit):
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.hostname == "shop.example.com" and path == "/robots.txt":
            return Fetched(status=200, body=b"User-agent: *\nDisallow: /cart/\n", content_type="text/plain")
        page = SAMPLE_PUBLIC.get(parts.hostname or "", {}).get(path)
        if page is None:
            return Fetched(status=404, final_url=url)
        return Fetched(status=200, body=page, content_type="text/html; charset=utf-8", final_url=url)

    return fetch


class SampleAdapters:
    def __init__(self) -> None:
        self.auto = SampleSite(manual=False)
        self.manual = SampleSite(manual=True)

    def build(self, site):
        if site.site_adapter is None:
            raise ValueError("Site adapter is not configured.")
        return self.manual if site.site_adapter.adapter_type == "manual" else self.auto


class SampleGoogle:
    """Connected from the start; connecting again just comes back to settings."""

    def __init__(self) -> None:
        self.connected = True

    def start(self, tenant_id):
        self.connected = True
        return "/app/#/settings"

    def connection(self, tenant_id):
        if not self.connected:
            return None
        return GoogleConnection(
            tenant_id=tenant_id, email="owner@example.test", scopes=[],
            credential_ref=tenant_credential_ref(tenant_id), connected_at=NOW,
        )

    def disconnect(self, tenant_id):
        self.connected = False
        return True


class SampleVault:
    def put(self, *, tenant_id, site_id, name, value):
        return SecretRef(provider=SecretProvider.DATABASE, key=f"{site_id}/{name}")


def daily(day: date, seed: int) -> tuple[int, int, float]:
    """Clicks, impressions, position for a day: a weekly swing and a slow rise."""
    age = (date.today() - day).days
    swing = 1 + 0.25 * math.sin(day.toordinal() / 1.1) + 0.3 * math.sin(day.toordinal() / 30)
    impressions = max(0, int((40 + seed * 37) * swing * (1 - age / 900)))
    clicks = max(0, int(impressions * (0.04 + 0.02 * ((seed % 3) + 1)) * (1 + 0.1 * math.sin(day.toordinal() / 9))))
    return min(clicks, impressions), impressions, impressions * (4 + (seed % 7) + math.sin(day.toordinal() / 14))


def metrics(clicks, impressions, position_sum) -> MetricValues:
    return MetricValues(
        clicks=clicks, impressions=impressions,
        ctr=clicks / impressions if impressions else None,
        position=position_sum / impressions if impressions else None,
    )


class SampleReports:
    """The warehouse's reports, computed from ``daily`` instead of read from SQL."""

    def __init__(self) -> None:
        self.rows = {
            (url, query): index + 1
            for index, (url, query) in enumerate((url, q) for url, queries in PAGES.items() for q in queries)
        }

    def _sum(self, keys, start, end, device):
        share = {"MOBILE": 0.7, "DESKTOP": 0.25, "TABLET": 0.05}.get(getattr(device, "value", device), 1)
        totals = [0, 0, 0.0]
        day = start
        while day <= end:
            for seed in keys:
                clicks, impressions, position_sum = daily(day, seed)
                totals[0] += int(clicks * share)
                totals[1] += int(impressions * share)
                totals[2] += position_sum * share
            day += timedelta(days=1)
        return totals

    def performance(self, *, site_id, property_url, start, end, interval="day", device=None):
        keys = list(self.rows.values())
        length = {"day": 1, "week": 7, "month": 30}[getattr(interval, "value", interval)]
        series = []
        day = start
        while day <= end:
            last = min(day + timedelta(days=length - 1), end)
            values = self._sum(keys, day, last, device)
            series.append(SeriesPoint(period_start=day, days_covered=(last - day).days + 1, **metrics(*values).model_dump()))
            day = last + timedelta(days=1)
        return PerformanceReport(
            site_id=site_id, start=start, end=end, interval=interval, device=device,
            totals=metrics(*self._sum(keys, start, end, device)),
            days_covered=(end - start).days + 1, days_in_range=(end - start).days + 1, series=series,
        )

    def _table(self, groups, *, site_id, start, end, device, sort, order, limit, offset, compare, contains):
        length = (end - start).days + 1
        rows = []
        for key, seeds in groups.items():
            if contains and contains.lower() not in key.lower():
                continue
            current = metrics(*self._sum(seeds, start, end, device))
            previous = None
            if compare:
                previous = metrics(*self._sum(seeds, start - timedelta(days=length), start - timedelta(days=1), device))
            rows.append(TableRow(key=key, current=current, previous=previous, search_volume=8000 if "خرید" in key else None))
        sort_key = getattr(sort, "value", sort)
        rows.sort(key=lambda row: getattr(row.current, sort_key) or 0, reverse=getattr(order, "value", order) == "desc")
        page = rows[offset:offset + limit]
        return TableReport(
            site_id=site_id, start=start, end=end, device=device, sort=sort, order=order, rows=page,
            next_offset=offset + limit if len(rows) > offset + limit else None,
            compared_with=(start - timedelta(days=length), start - timedelta(days=1)) if compare else None,
            previous_days_covered=length if compare else None, previous_days_in_range=length if compare else None,
        )

    def pages(self, *, site_id, property_url, start, end, device=None, sort="clicks", order="desc",
              limit=50, offset=0, compare=False, contains=None):
        groups: dict[str, list[int]] = {}
        for (url, _query), seed in self.rows.items():
            groups.setdefault(url, []).append(seed)
        return self._table(groups, site_id=site_id, start=start, end=end, device=device, sort=sort, order=order,
                           limit=limit, offset=offset, compare=compare, contains=contains)

    def queries(self, *, site_id, property_url, start, end, device=None, page_url=None, sort="clicks",
                order="desc", limit=50, offset=0, compare=False, contains=None):
        groups: dict[str, list[int]] = {}
        for (url, query), seed in self.rows.items():
            if page_url is None or url == page_url:
                groups.setdefault(query, []).append(seed)
        return self._table(groups, site_id=site_id, start=start, end=end, device=device, sort=sort, order=order,
                           limit=limit, offset=offset, compare=compare, contains=contains)


class SampleWarehouse:
    """Every settled day stored, except a few a little while ago, which failed."""

    def __init__(self, shop_id: str, reports: SampleReports) -> None:
        self.shop_id = shop_id
        self.reports = reports

    def ledger(self, *, site_id, start, end):
        if site_id != self.shop_id:
            return []
        entries = []
        day = max(start, end - timedelta(days=479))
        while day <= end:
            age = (end - day).days
            entries.append(SyncDayStatus(
                site_id=site_id, day=day, property_url="sc-domain:shop.example.com",
                status=SyncStatus.SYNCED, totals_rows=3, page_rows=4, query_rows=11,
                page_rows_capped=False, query_rows_capped=False,
                fetched_at=NOW - timedelta(days=min(age, 12), hours=age % 5),
            ))
            day += timedelta(days=1)
        return entries

    def query_days(self, *, site_id, url, query, start, end):
        seed = self.reports.rows.get((url, query), 1)
        result = {}
        day = start
        while day <= end:
            clicks, impressions, position_sum = daily(day, seed)
            if day >= CHANGE_DAY + timedelta(days=8):
                clicks = int(clicks * BOOST.get(query, 1))
            result[day] = (min(clicks, impressions), impressions, position_sum)
            day += timedelta(days=1)
        return result

    def page_days(self, *, site_id, url, start, end):
        return {start + timedelta(days=n) for n in range((end - start).days + 1)}


# The sample settled changes: applied long ago, with these effects on clicks.
CHANGE_DAY = date.today() - timedelta(days=60)
BOOST = {"کفش مردانه چرم": 1.4, "قیمت کفش مردانه": 1.05, "خرید کیف زنانه چرم": 0.6}


def seed(repository, sites, actions, adapters, warehouse):
    shop = Site(
        site_id="site-shop", principal_id=TENANT, name="فروشگاه نمونه", base_url=f"{SHOP}/",
        gsc=GSCConnectionConfig(
            property_url="sc-domain:shop.example.com", credential_ref=tenant_credential_ref(TENANT),
            auth_mode="oauth_refresh_token",
        ),
        site_adapter=SiteAdapterConnection(adapter_type="hoshyarseo"),
    )
    hand_built = Site(
        site_id="site-manual", principal_id=TENANT, name="سایت Next.js نمونه", base_url="https://next.example.com/",
        gsc=shop.gsc.model_copy(update={"property_url": "sc-domain:next.example.com"}),
        site_adapter=SiteAdapterConnection(adapter_type="manual"),
    )
    sites.create(shop)
    sites.create(hand_built)

    men, bags = f"{SHOP}/men-shoes", f"{SHOP}/women-bags"
    feedback = FeedbackStore(repository)
    execute = build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=adapters)
    rollback = build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=adapters)

    def applied(run, url, query, current, title, *, days_ago=0, site_id="site-shop"):
        adapters.auto.titles[url] = current
        actions.create(approve(proposal(run, site_id, url, query, current, title), actor=TENANT))
        execute({"action_id": f"action:{run}"})
        managed = actions.get(f"action:{run}")
        return actions.update(managed.model_copy(update={
            "applied": managed.applied.model_copy(update={"applied_at": NOW - timedelta(days=days_ago)}),
        }))

    actions.create(proposal(
        "sample-waiting", "site-shop", men, "خرید کفش مردانه", "کفش مردانه | فروشگاه نمونه",
        "خرید کفش مردانه اصل با ارسال رایگان | فروشگاه نمونه",
        "کفش مردانه چرم و اسپرت، ضمانت اصالت | فروشگاه نمونه",
        "خرید اینترنتی کفش مردانه با تخفیف ویژه | فروشگاه نمونه",
    ))
    actions.create(proposal(
        "sample-waiting-2", "site-shop", bags, "کیف زنانه", "کیف زنانه",
        "کیف زنانه شیک و چرم طبیعی | خرید با ضمانت بازگشت",
        "خرید کیف زنانه مجلسی و روزمره | فروشگاه نمونه",
    ))
    adapters.auto.titles[men] = "کفش مردانه | فروشگاه نمونه"
    adapters.auto.titles[bags] = "کیف زنانه"
    # Settled samples are on pages of their own: one page has one title, and
    # the executor rightly refuses a proposal whose page has moved on.
    applied("sample-measuring", f"{SHOP}/", "فروشگاه کفش", "فروشگاه نمونه", "فروشگاه کفش نمونه | ارسال به سراسر ایران", days_ago=9)
    applied("sample-success", f"{SHOP}/leather", "کفش مردانه چرم", "کفش مردانه چرم", "کفش مردانه چرم طبیعی | خرید با ضمانت", days_ago=60)
    applied("sample-band", f"{SHOP}/prices", "قیمت کفش مردانه", "قیمت کفش", "قیمت روز کفش مردانه | مقایسه و خرید", days_ago=60)
    applied("sample-failed", f"{SHOP}/bags-leather", "خرید کیف زنانه چرم", "کیف چرم زنانه", "کیف چرم زنانه دست‌ساز", days_ago=60)
    MeasurementRunner(
        repository=repository, action_store=actions, feedback_store=feedback, site_store=sites, warehouse=warehouse,
        transaction=repository.transaction,
    ).run_round()
    applied("sample-undone", f"{SHOP}/bags-shoulder", "کیف دوشی زنانه", "کیف دوشی", "کیف دوشی زنانه چرم", days_ago=20)
    rollback({"action_id": "action:sample-undone", "actor": TENANT})

    actions.create(proposal(
        "sample-manual-waiting", "site-manual", "https://next.example.com/tennis-court", "زمین تنیس خاکی تهران",
        "زمین تنیس", "رزرو زمین تنیس خاکی در تهران | قیمت و آدرس",
    ))
    actions.create(approve(proposal(
        "sample-manual-apply", "site-manual", "https://next.example.com/lessons", "آموزش تنیس مبتدی",
        "آموزش تنیس", "آموزش تنیس برای مبتدیان | کلاس خصوصی و گروهی",
    ), actor=TENANT))
    adapters.manual.titles["https://next.example.com/lessons"] = "آموزش تنیس"
    execute({"action_id": "action:sample-manual-apply"})


def sample_run_handler(*, runs, actions, sites, adapters):
    """An analysis that always proposes: the real one needs Google, SERP and a model."""

    def handler(payload):
        run = runs.get(str(payload["run_id"]))
        running = run.start()
        runs.update(running)
        page = adapters.build(sites.get(run.site_id)).read_page(site_id=run.site_id, normalized_url=run.normalized_url)
        managed = proposal(
            run.run_id, run.site_id, run.normalized_url, run.normalized_query, page.title,
            f"{run.normalized_query} | پیشنهاد نمونه", f"{run.normalized_query} با تخفیف", f"خرید {run.normalized_query}",
        )
        actions.create(managed)
        runs.update(running.complete(result={
            "status": "completed", "title_proposal_status": "completed", "action_id": managed.action_id,
        }))
        return {"run_id": run.run_id}

    return handler


def build():
    repository = DevRepository()
    tenants, sites, actions = TenantStore(repository), SiteStore(repository), ManagedActionStore(repository)
    runs, jobs = RunStore(repository), JobStore(repository)
    tenants.create(Tenant(tenant_id=TENANT, name="فروشگاه نمونه (توسعه)"))
    auth = AuthService(accounts=AccountStore(repository), sessions=SessionStore(repository), tenants=tenants)
    auth.create_account(tenant_id=TENANT, email="dev@example.test", password="dev-password")

    adapters = SampleAdapters()
    reports = SampleReports()
    warehouse = SampleWarehouse("site-shop", reports)
    seed(repository, sites, actions, adapters, warehouse)

    handlers = JobHandlerRegistry()
    handlers.register(EXECUTE_ACTION_JOB_TYPE, build_execute_action_handler(action_store=actions, site_store=sites, adapter_factory=adapters))
    handlers.register(ROLLBACK_ACTION_JOB_TYPE, build_rollback_action_handler(action_store=actions, site_store=sites, adapter_factory=adapters))
    handlers.register(SEO_RUN_JOB_TYPE, sample_run_handler(runs=runs, actions=actions, sites=sites, adapters=adapters))
    scans, files = SiteScanStore(repository), SiteFileStore(repository)
    public = sample_fetcher()
    handlers.register(SITE_SCAN_JOB_TYPE, build_site_scan_handler(
        site_store=sites, scan_store=scans, file_store=files,
        crawler_factory=lambda: SiteCrawler(fetcher=public, delay_seconds=0.05),
    ))
    handlers.register(SITE_FILE_JOB_TYPE, build_site_file_handler(
        site_store=sites, file_store=files, fetcher=public,
    ))
    scheduler = JobScheduler(store=jobs, handlers=handlers)
    threading.Thread(
        target=scheduler.run_forever, kwargs={"poll_interval_seconds": 0.5, "stop_event": threading.Event()}, daemon=True,
    ).start()

    app = create_app(APIDependencies(
        scheduler=scheduler,
        authenticator=APIKeyAuthenticator("dev-only-key", principal_id=TENANT),
        site_store=sites, run_store=runs, action_store=actions,
        transaction_factory=repository.transaction,
        adapter_factory=adapters, google_oauth=SampleGoogle(), vault=SampleVault(),
        gsc_property_lister=lambda *, auth_mode, credential_ref: [
            SimpleNamespace(
                site_url=url, display_name=url, permission_level="siteOwner", is_domain_property=True, readable=True,
                matches=lambda wanted, url=url: wanted == url,
            )
            for url in ("sc-domain:shop.example.com", "sc-domain:next.example.com")
        ],
        warehouse=warehouse, warehouse_reports=reports, auth=auth, scan_store=scans, file_store=files,
        dashboard_url="/app/", tenant_name=lambda _tenant: "فروشگاه نمونه (توسعه)",
    ))

    @app.get("/app/downloads/hoshyarseo-connector.zip", include_in_schema=False)
    def connector_zip():
        source = ROOT / "integrations" / "wordpress"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
        return Response(buffer.getvalue(), media_type="application/zip")

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/app/")

    # The same policy nginx sends in production, so a violation shows up here.
    policy = re.search(r'Content-Security-Policy "([^"]+)"', (ROOT / "deploy" / "nginx-app.conf").read_text(encoding="utf-8")).group(1)

    @app.middleware("http")
    async def content_security_policy(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/app"):
            response.headers["Content-Security-Policy"] = policy
        return response

    app.mount("/app", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app


if __name__ == "__main__":
    os.chdir(ROOT)
    print(f"Sample window ends {sync_window(NOW)[1]}. Sign in with dev@example.test / dev-password")
    uvicorn.run(build(), host="127.0.0.1", port=PORT, log_level="warning")
