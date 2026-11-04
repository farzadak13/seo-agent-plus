"""Suggesting target words, proposing a title for one, and checking where each ranks.

Nothing here needs Search Console. The words come from what the scan read on the
site and what the owner says the business is; a title proposal goes through the
same comparison with Google's results and the same approval as any other; and
progress is where the site's page stands in Google for each word, looked up
once a week.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.action.approval import propose_title_change
from app.action.planner import build_action
from app.action.store import ManagedActionStore
from app.models.opportunities import OpportunityType
from app.models.runs import SEORunStatus
from app.models.snapshots import SnapshotMetadata
from app.models.strategies import Strategy, StrategyStatus, StrategyType
from app.models.target_keywords import (
    TARGET_KEYWORD_ORIGIN,
    KeywordStatus,
    RankCheck,
    SuggestionStatus,
    TargetKeyword,
    TargetKeywordSet,
)
from app.models.title_proposal import TitleProposalStatus
from app.net.guard import same_site
from app.onboarding.site_store import SiteStore
from app.reasoning.keywords import KeywordSuggester, PageForKeywords
from app.runs.store import RunStore
from app.scan.crawler import _identity
from app.scan.issues import is_listable
from app.scan.store import SiteScanStore
from app.serp.provider import SERPProvider, SERPProviderError
from app.targets.store import TargetKeywordStore

log = logging.getLogger(__name__)

TARGET_SUGGEST_JOB_TYPE = "target_keywords"
TARGET_TITLE_JOB_TYPE = "target_title"
# The run's candidate id says where its strategy came from.
TARGET_CANDIDATE = TARGET_KEYWORD_ORIGIN
RULE_VERSION = "target-keywords-v1"
RANK_EVERY = timedelta(days=7)
# Often enough that a word comes due within the hour; each round asks only for those due.
RANK_ROUND_SECONDS = 3600
KEPT_CHECKS = 26
# Dismissed words included: the record is rewritten whole on every change.
MAX_WORDS = 100


def make_room(keywords: list[TargetKeyword], wanted: int, *, limit: int | None = None) -> list[TargetKeyword]:
    """The list with room for ``wanted`` more words, made by forgetting the oldest dismissed ones.

    A dismissed word is kept only so it is not suggested again; past the
    limit the oldest of them go, and a full list never locks the site out.
    """
    limit = MAX_WORDS if limit is None else limit
    excess = len(keywords) + wanted - limit
    if excess <= 0:
        return list(keywords)
    dismissed = sorted(
        (item for item in keywords if item.status == KeywordStatus.DISMISSED), key=lambda item: item.added_at
    )
    forgotten = {item.keyword for item in dismissed[:excess]}
    return [item for item in keywords if item.keyword not in forgotten]


def _is_removed(site) -> bool:
    return getattr(site.status, "value", site.status) == "removed"


def scan_pages(scan) -> list[PageForKeywords]:
    """The pages worth a word: those a sitemap would list."""
    if scan is None:
        return []
    return [PageForKeywords(url=page.url, title=page.title, h1=page.h1) for page in scan.pages if is_listable(page)]


def build_target_suggestion_handler(
    *,
    site_store: SiteStore,
    scan_store: SiteScanStore,
    target_store: TargetKeywordStore,
    suggester: KeywordSuggester,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def mine(current: TargetKeywordSet, job_id) -> bool:
        return current.suggestion_status == SuggestionStatus.RUNNING and current.suggestion_job_id == job_id

    def fail(site_id: str, job_id, error: str) -> None:
        def change(current: TargetKeywordSet):
            if not mine(current, job_id):
                return None
            return current.model_copy(update={"suggestion_status": SuggestionStatus.FAILED, "suggestion_error": error, "updated_at": clock()})

        target_store.update(site_id, change)

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        site_id = str(payload["site_id"])
        job_id = payload.get("job_id")
        current = target_store.find(site_id)
        if current is None or not mine(current, job_id):
            return {"site_id": site_id, "skipped": True}
        # Whatever goes wrong from here, the set must not be left "running":
        # the job is not retried, and the button would stay off.
        try:
            site = site_store.get(site_id)
            pages = scan_pages(scan_store.find(site_id))
            if _is_removed(site) or not pages:
                fail(site_id, job_id, "no_pages")
                return {"site_id": site_id, "skipped": True}
            found = suggester.suggest(description=current.description, seeds=current.seeds, pages=pages, identity=_identity)
        except Exception as exc:
            log.warning("target keywords: suggestion for %s failed (%s)", site_id, type(exc).__name__)
            fail(site_id, job_id, "model")
            return {"site_id": site_id, "status": "failed"}

        added: list[int] = []

        def change(latest: TargetKeywordSet):
            # A newer request took over while the model was answering: its answer wins.
            if not mine(latest, job_id):
                return None
            # What the customer decided stays; the model's earlier, undecided ideas
            # give way to its new ones.
            decided = [item for item in latest.keywords if item.status != KeywordStatus.SUGGESTED]
            taken = {item.keyword for item in decided}
            fresh = [
                TargetKeyword(keyword=item.keyword, url=item.url, intent=item.intent, reason=item.reason or None)
                for item in found
                if item.keyword not in taken
            ]
            decided = make_room(decided, len(fresh))
            fresh = fresh[: max(0, MAX_WORDS - len(decided))]
            added[:] = [len(fresh)]
            return latest.model_copy(
                update={
                    "keywords": [*decided, *fresh],
                    "suggestion_status": SuggestionStatus.IDLE,
                    "suggestion_error": None,
                    "suggested_at": clock(),
                    "updated_at": clock(),
                }
            )

        try:
            target_store.update(site_id, change)
        except Exception as exc:
            log.warning("target keywords: saving suggestions for %s failed (%s)", site_id, type(exc).__name__)
            fail(site_id, job_id, "save")
            raise
        return {"site_id": site_id, "suggested": added[0] if added else 0}

    return handler


def target_strategy(*, run, now: datetime) -> Strategy:
    """A title strategy for a word the site has no data for, so no score is claimed."""
    return Strategy(
        strategy_id=f"target:{run.run_id}",
        opportunity_id=f"target:{run.site_id}:{run.normalized_query}",
        site_id=run.site_id,
        normalized_url=run.normalized_url,
        normalized_query=run.normalized_query,
        opportunity_type=OpportunityType.VISIBILITY_GROWTH,
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=0.5,
        expected_impact_score=0.5,
        effort_score=0.2,
        risk_score=0.1,
        priority_score=0.5,
        reasons=["target_keyword_chosen_by_customer"],
        # The SERP check then does not ask that the page already ranks: a new
        # page has not had the chance.
        evidence={"origin": TARGET_KEYWORD_ORIGIN},
        snapshot=SnapshotMetadata(
            snapshot_id=f"target:{run.run_id}",
            data_snapshot_id="no-search-console-data",
            rule_version=RULE_VERSION,
            config_version=RULE_VERSION,
            generated_at=now,
        ),
    )


def build_target_title_handler(
    *,
    site_store: SiteStore,
    run_store: RunStore,
    action_store: ManagedActionStore,
    title_workflow,
    proposal_store=None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def existing_proposal(run_id: str):
        """A proposal an earlier attempt of this run already paid for."""
        if proposal_store is None:
            return None
        try:
            return proposal_store.get(f"proposal:{run_id}")
        except KeyError:
            return None

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        run = run_store.get(str(payload["run_id"]))
        if run.status == SEORunStatus.COMPLETED:
            return {"run_id": run.run_id, "status": run.status.value}
        if run.status in {SEORunStatus.FAILED, SEORunStatus.RUNNING}:
            run = run.model_copy(update={"status": SEORunStatus.QUEUED, "completed_at": None, "error": None})
        site = site_store.get(run.site_id)
        running = run_store.update(run.start())
        try:
            strategy = target_strategy(run=running, now=running.created_at)
            proposal = existing_proposal(run.run_id) or title_workflow.run(run_id=run.run_id, site=site, strategy=strategy)
            result: dict[str, Any] = {
                "status": "completed",
                "title_proposal_id": proposal.proposal_id,
                "title_proposal_status": proposal.status.value,
                "title_proposal_reasons": list(proposal.reasons),
            }
            if proposal.status == TitleProposalStatus.COMPLETED:
                managed = propose_title_change(
                    action=build_action(strategy=strategy, action_id=f"candidate-{run.run_id}"),
                    run_id=run.run_id,
                    tenant_id=run.principal_id,
                    proposal_id=proposal.proposal_id,
                    proposed_title=proposal.selected_title,
                    current_title=proposal.investigation.target_title,
                    provider_id=proposal.provider_id,
                    alternatives=[candidate.title for candidate in getattr(proposal.reasoning, "candidates", None) or []],
                )
                existing = action_store.find(managed.action_id)
                result["action_id"] = (existing or action_store.create(managed)).action_id
            run_store.update(running.complete(result=result))
            return {"run_id": run.run_id, **result}
        except Exception as exc:
            run_store.update(running.fail(str(exc)[:500]))
            raise

    return handler


class RankTracker:
    """Where each confirmed word's page stands in Google, once a week.

    One SERP request per word per week, behind the cache and daily budget of
    the SERP provider, so the cost is the number of words. A check is dated by
    when Google gave the answer, not by when it was read from the cache: an old
    answer is not news, and is not recorded as this week's.
    """

    def __init__(
        self,
        *,
        target_store: TargetKeywordStore,
        site_store: SiteStore,
        serp_provider: SERPProvider,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        per_round: int = 50,
    ) -> None:
        self._targets = target_store
        self._sites = site_store
        self._serp = serp_provider
        self._clock = clock
        self._per_round = per_round

    def run_round(self) -> dict[str, int]:
        now = self._clock()
        checked = 0
        for keywords in self._targets.list_all():
            if checked >= self._per_round:
                break
            try:
                site = self._sites.get(keywords.site_id)
            except Exception:
                continue
            if _is_removed(site):
                continue
            due = [
                item
                for item in keywords.keywords
                if item.status == KeywordStatus.CONFIRMED and (not item.checks or now - item.checks[-1].checked_at >= RANK_EVERY)
            ]
            if not due:
                continue
            found: dict[str, RankCheck] = {}
            for item in due[: self._per_round - checked]:
                try:
                    snapshot = self._serp.search(query=item.keyword)
                except SERPProviderError as exc:
                    # Out of budget or down: the next round picks up where this stopped.
                    log.warning("rank check: SERP could not answer (%s); stopping this round", type(exc).__name__)
                    self._save(keywords.site_id, found)
                    return {"checked": checked}
                checked += 1
                if item.checks and snapshot.fetched_at <= item.checks[-1].checked_at:
                    continue
                mine = next((result for result in snapshot.results if same_site(str(site.base_url), result.url)), None)
                found[item.keyword] = RankCheck(
                    checked_at=snapshot.fetched_at, position=mine.position if mine else None, url=mine.url if mine else None
                )
            self._save(keywords.site_id, found)
        return {"checked": checked}

    def _save(self, site_id: str, found: dict[str, RankCheck]) -> None:
        if not found:
            return

        def change(latest: TargetKeywordSet):
            # Worked out from the set as it is now: the customer may have changed it while we looked.
            updated = [
                item.model_copy(update={"checks": [*item.checks, found[item.keyword]][-KEPT_CHECKS:]})
                if item.keyword in found and (not item.checks or found[item.keyword].checked_at > item.checks[-1].checked_at)
                else item
                for item in latest.keywords
            ]
            if updated == latest.keywords:
                return None  # nothing newer than what is there: no new version
            return latest.model_copy(update={"keywords": updated})

        try:
            self._targets.update(site_id, change)
        except Exception as exc:
            log.warning("rank check: could not save for %s (%s)", site_id, type(exc).__name__)
