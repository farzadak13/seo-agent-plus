"""Target words for a site with no Search Console data: the brief, the list, a title for one."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.account_routes import limits_of, require_plan
from app.api.schemas import RunResponse
from app.models.jobs import Job
from app.models.runs import SEORun, SEORunStatus
from app.models.site_scan import ScanStatus
from app.models.target_keywords import (
    KeywordSource,
    KeywordStatus,
    SuggestionStatus,
    TargetKeyword,
    TargetKeywordSet,
)
from app.normalization.query import normalize_query
from app.normalization.url import canonicalize_url
from app.persistence.contracts import PersistenceConflictError, PersistenceNotFoundError
from app.scan.crawler import _identity
from app.targets.handler import TARGET_CANDIDATE, TARGET_SUGGEST_JOB_TYPE, TARGET_TITLE_JOB_TYPE, make_room, scan_pages

# Each suggestion and each title is a model call, and a title a Google search
# from the budget every customer shares: so per site, per word and per tenant.
SUGGEST_COOLDOWN = timedelta(minutes=10)
SUGGEST_STALE_AFTER = timedelta(minutes=15)
SUGGESTS_PER_TENANT_PER_DAY = 10
TITLE_COOLDOWN = timedelta(minutes=30)
TITLE_STALE_AFTER = timedelta(minutes=15)
TITLES_PER_TENANT_PER_DAY = 20
KEPT_REQUESTS = 50
# Each confirmed word costs one Google search a week; how many a site may keep
# is the plan's (Plan.targets). MAX_CONFIRMED is the cap without a plan.
MAX_CONFIRMED = 20

Seed = Annotated[str, Field(max_length=80)]


class BriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=1000)
    seeds: list[Seed] = Field(default_factory=list, max_length=10)


class KeywordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=2000)


class KeywordStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=80)
    status: KeywordStatus


class PageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None


class TargetKeywordsView(TargetKeywordSet):
    """The set, and the pages a word can be for: those the latest scan read."""

    pages: list[PageChoice] = Field(default_factory=list)


def _within_day(times: list[datetime], now: datetime) -> int:
    return sum(1 for moment in times if now - moment < timedelta(days=1))


def register_target_routes(app: FastAPI, dependencies, *, principal_id: Callable, owned_site: Callable) -> None:
    def targets():
        if dependencies.target_store is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Target keywords are not configured.")
        return dependencies.target_store

    def latest_scan(site_id: str):
        scan = dependencies.scan_store.completed(site_id) if dependencies.scan_store is not None else None
        return scan if scan is not None and scan.status == ScanStatus.COMPLETED else None

    def pages_of(site_id: str):
        scan = latest_scan(site_id)
        if scan is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scan the site first; its pages are what the words are for.")
        return scan_pages(scan)

    def save(keywords: TargetKeywordSet, version: int, *jobs: Job, run: SEORun | None = None) -> TargetKeywordSet:
        """Written only over the version this request read: a change made meanwhile is not undone."""
        updated = keywords.model_copy(update={"updated_at": dependencies.clock()})
        try:
            with dependencies.transaction_factory():
                targets().save(updated, expected_version=version)
                if run is not None:
                    dependencies.run_store.create(run)
                for job in jobs:
                    dependencies.scheduler.store.create(job)
        except PersistenceConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The target keywords changed while this request was being handled; reload and try again.",
            ) from exc
        return updated

    def find_keyword(keywords: TargetKeywordSet, word: str) -> TargetKeyword:
        wanted = normalize_query(word)
        for item in keywords.keywords:
            if item.keyword == wanted:
                return item
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such target keyword.")

    def confirmed_count(keywords: TargetKeywordSet) -> int:
        return sum(1 for item in keywords.keywords if item.status == KeywordStatus.CONFIRMED)

    def tenant_requests(site, field: str, now: datetime) -> int:
        return sum(_within_day(getattr(each, field), now) for each in targets().for_tenant(site.principal_id))

    @app.get("/v1/sites/{site_id}/target-keywords", response_model=TargetKeywordsView)
    def get_targets(site_id: str, authenticated_principal_id: str = Depends(principal_id)) -> TargetKeywordsView:
        site = owned_site(site_id, authenticated_principal_id)
        keywords, _ = targets().read_or_new(site)
        # A job lost to a restart must not keep the button off for ever; the
        # server takes a new request by then.
        if keywords.suggestion_status == SuggestionStatus.RUNNING and dependencies.clock() - keywords.updated_at >= SUGGEST_STALE_AFTER:
            keywords = keywords.model_copy(update={"suggestion_status": SuggestionStatus.FAILED, "suggestion_error": "lost"})
        scan = latest_scan(site_id)
        pages = scan_pages(scan) if scan is not None else []
        return TargetKeywordsView(
            **keywords.model_dump(),
            pages=[PageChoice(url=page.url, title=page.title) for page in pages],
        )

    @app.put("/v1/sites/{site_id}/target-keywords/brief", response_model=TargetKeywordSet)
    def set_brief(site_id: str, request: BriefRequest, authenticated_principal_id: str = Depends(principal_id)) -> TargetKeywordSet:
        site = owned_site(site_id, authenticated_principal_id)
        seeds = list(dict.fromkeys(word for word in (normalize_query(seed) for seed in request.seeds) if word))
        keywords, version = targets().read_or_new(site)
        return save(keywords.model_copy(update={"description": request.description.strip(), "seeds": seeds}), version)

    @app.post("/v1/sites/{site_id}/target-keywords/suggest", response_model=TargetKeywordSet, status_code=status.HTTP_202_ACCEPTED)
    def suggest(site_id: str, authenticated_principal_id: str = Depends(principal_id)) -> TargetKeywordSet:
        site = owned_site(site_id, authenticated_principal_id)
        require_plan(dependencies, authenticated_principal_id)
        if not pages_of(site_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The scan found no page to suggest words for.")
        keywords, version = targets().read_or_new(site)
        now = dependencies.clock()
        if keywords.suggestion_status == SuggestionStatus.RUNNING and now - keywords.updated_at < SUGGEST_STALE_AFTER:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Suggestions for this site are already being made.")
        # From the request, not the answer: a failed one cost the call too.
        if keywords.suggest_requests and now - keywords.suggest_requests[-1] < SUGGEST_COOLDOWN:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Words were suggested a moment ago.")
        if tenant_requests(site, "suggest_requests", now) >= SUGGESTS_PER_TENANT_PER_DAY:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many suggestions asked for today.")
        job_id = dependencies.job_id_factory()
        job = Job(
            job_id=job_id,
            job_type=TARGET_SUGGEST_JOB_TYPE,
            principal_id=authenticated_principal_id,
            payload={"site_id": site_id, "job_id": job_id},
            max_attempts=1,
        )
        started = keywords.model_copy(
            update={
                "suggestion_status": SuggestionStatus.RUNNING,
                "suggestion_job_id": job_id,
                "suggestion_error": None,
                "suggest_requests": [*keywords.suggest_requests, now][-KEPT_REQUESTS:],
            }
        )
        return save(started, version, job)

    @app.post("/v1/sites/{site_id}/target-keywords", response_model=TargetKeywordSet, status_code=status.HTTP_201_CREATED)
    def add_keyword(site_id: str, request: KeywordRequest, authenticated_principal_id: str = Depends(principal_id)) -> TargetKeywordSet:
        """A word the customer types in, for a page the scan read (the home page if none is named)."""
        site = owned_site(site_id, authenticated_principal_id)
        require_plan(dependencies, authenticated_principal_id)
        pages = pages_of(site_id)
        word = normalize_query(request.keyword)
        if not word or len(word) > 80:
            raise HTTPException(status_code=422, detail="The keyword is empty.")
        known = {_identity(page.url): page.url for page in pages}
        url = known.get(_identity(request.url)) if request.url else (pages[0].url if pages else None)
        if url is None:
            raise HTTPException(status_code=422, detail="That page is not one the scan read on this site.")
        keywords, version = targets().read_or_new(site)
        existing = next((item for item in keywords.keywords if item.keyword == word), None)
        if (existing is None or existing.status != KeywordStatus.CONFIRMED) and confirmed_count(keywords) >= limits_of(dependencies, site.principal_id).targets:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This site already has the most target keywords it can have.")
        if existing is not None:
            # Its history stays: where the site stood for this word is still true.
            added = existing.model_copy(update={"url": url, "status": KeywordStatus.CONFIRMED, "source": KeywordSource.CUSTOMER})
            listed = [added if item.keyword == word else item for item in keywords.keywords]
        else:
            listed = [*make_room(keywords.keywords, 1), TargetKeyword(keyword=word, url=url, status=KeywordStatus.CONFIRMED, source=KeywordSource.CUSTOMER)]
        return save(keywords.model_copy(update={"keywords": listed}), version)

    @app.post("/v1/sites/{site_id}/target-keywords/status", response_model=TargetKeywordSet)
    def set_status(site_id: str, request: KeywordStatusRequest, authenticated_principal_id: str = Depends(principal_id)) -> TargetKeywordSet:
        site = owned_site(site_id, authenticated_principal_id)
        keywords, version = targets().read_or_new(site)
        item = find_keyword(keywords, request.keyword)
        if request.status == KeywordStatus.CONFIRMED and item.status != KeywordStatus.CONFIRMED and confirmed_count(keywords) >= limits_of(dependencies, site.principal_id).targets:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This site already has the most target keywords it can have.")
        updated = [entry.model_copy(update={"status": request.status}) if entry.keyword == item.keyword else entry for entry in keywords.keywords]
        return save(keywords.model_copy(update={"keywords": updated}), version)

    def title_in_flight(item: TargetKeyword, now: datetime) -> bool:
        if item.title_run_id is None or item.title_requested_at is None or now - item.title_requested_at >= TITLE_STALE_AFTER:
            return False
        try:
            run = dependencies.run_store.get(item.title_run_id)
        except PersistenceNotFoundError:
            return False
        return run.status in {SEORunStatus.QUEUED, SEORunStatus.RUNNING}

    @app.post("/v1/sites/{site_id}/target-keywords/title", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
    def propose_title(site_id: str, request: KeywordRequest, authenticated_principal_id: str = Depends(principal_id)) -> RunResponse:
        """A title for the page this word is for, compared with Google's results, waiting for approval."""
        site = owned_site(site_id, authenticated_principal_id)
        require_plan(dependencies, authenticated_principal_id)
        if site.site_adapter is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect the site first, so its page can be read and changed.")
        if dependencies.run_store is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Runs are not configured.")
        keywords, version = targets().read_or_new(site)
        item = find_keyword(keywords, request.keyword)
        if item.status != KeywordStatus.CONFIRMED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Keep the keyword first, then ask for a title.")
        now = dependencies.clock()
        if title_in_flight(item, now):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A title for this keyword is already being made.")
        if item.title_requested_at is not None and now - item.title_requested_at < TITLE_COOLDOWN:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="A title for this keyword was asked for a moment ago.")
        if tenant_requests(site, "title_requests", now) >= TITLES_PER_TENANT_PER_DAY:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many titles asked for today.")
        today = now.date().isoformat()
        run_id = dependencies.run_id_factory()
        job = Job(
            job_id=dependencies.job_id_factory(),
            job_type=TARGET_TITLE_JOB_TYPE,
            principal_id=authenticated_principal_id,
            payload={"run_id": run_id},
            max_attempts=2,
        )
        run = SEORun(
            run_id=run_id,
            site_id=site_id,
            principal_id=authenticated_principal_id,
            job_id=job.job_id,
            start_date=today,
            end_date=today,
            # The form every stored page address takes, so Search Console rows
            # match it once the site has them.
            normalized_url=canonicalize_url(item.url),
            normalized_query=item.keyword,
            candidate_id=TARGET_CANDIDATE,
        )
        asked = item.model_copy(update={"title_run_id": run_id, "title_requested_at": now})
        updated = keywords.model_copy(
            update={
                "keywords": [asked if entry.keyword == item.keyword else entry for entry in keywords.keywords],
                "title_requests": [*keywords.title_requests, now][-KEPT_REQUESTS:],
            }
        )
        save(updated, version, job, run=run)
        return RunResponse.from_run(run)
