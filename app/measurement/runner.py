"""Stage 39 — measure what a change did, settle the action, keep what it taught.

An action whose change was verified on the site waits in
MEASUREMENT_WINDOW_ACTIVE. Each round looks at every such action and, once
the data is there, compares the weeks before the change with the weeks after
it, decides the outcome, moves the action to SUCCEEDED, FAILED or
INCONCLUSIVE, and records one feedback event for the tenant's learning.

The windows are Search Console days (Pacific), the calendar the warehouse is
kept in:

    baseline: the window_days before the change day
    settle:   the change day and settle_days after it, in neither window,
              while Google recrawls the page and rankings move
    current:  the window_days after that

Only final, stored days are read. The action waits until every day of both
windows is stored; a site whose sync has stopped would keep it waiting for
ever, so deadline_days after the current window could have been complete it
is measured with what there is, which usually means INCONCLUSIVE. That is the
honest answer for a change nobody can see the effect of.

The outcome, the action's new status and the feedback event are written in
one transaction, under ids derived from the action: a round repeated after a
crash, or by a second server that took the worker lease over, cannot settle
an action twice or count it twice.

A rollback the customer has asked for comes first. An action with a pending
request is left alone until the job has finished; a request landing during
a round extends the action's history, so the round's write fails its history
check and the next round sees the request.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.action.approval import SYSTEM_ACTOR
from app.action.lifecycle import mark_evaluating, mark_outcome
from app.action.store import ACTION_AGGREGATE_TYPE, ManagedActionStore
from app.ingestion.calendar import GSC_DATA_LAG_DAYS
from app.learning.engine import build_feedback_event
from app.learning.store import FeedbackStore, feedback_id_for
from app.measurement.engine import measure_action
from app.measurement.series import build_observations, window_days
from app.models.actions import ActionStatus, ActionType
from app.models.managed_action import ManagedAction
from app.models.measurement import MeasurementResult
from app.models.outcomes import OutcomeEvaluationPolicy, OutcomeEvaluationResult, OutcomeMetric
from app.models.persistence import PersistenceRecord
from app.models.snapshots import SnapshotMetadata
from app.outcome.engine import evaluate_outcome
from app.onboarding.site_store import SiteStore
from app.persistence.contracts import PersistenceConflictError, PersistenceNotFoundError
from app.warehouse.planner import search_console_today, sync_window


log = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 28
DEFAULT_SETTLE_DAYS = 7
DEFAULT_DEADLINE_DAYS = 14
ROUND_INTERVAL_SECONDS = 3600.0

OUTCOME_AGGREGATE_TYPE = "action_outcome"
OUTCOME_SCHEMA_VERSION = 1
# v2: a change inside the noise band is INCONCLUSIVE, no longer FAILED.
CONFIG_VERSION = "measurement-stage39-v2"

# A new title is for the click: CTR is what it can move. 10% either way is
# well outside a month's ordinary noise on a query with steady impressions,
# and a change inside that band says nothing either way: INCONCLUSIVE.
POLICIES: dict[ActionType, OutcomeEvaluationPolicy] = {
    ActionType.OPTIMIZE_TITLE: OutcomeEvaluationPolicy(
        primary_metric=OutcomeMetric.CTR,
        min_relative_improvement=0.10,
        max_relative_regression=0.10,
        inconclusive_within_band=True,
    ),
}

_WATCHED = frozenset({ActionStatus.MEASUREMENT_WINDOW_ACTIVE, ActionStatus.EVALUATING})

# How a rollback job ends without rolling back (app.action.executor). Its other
# endings move the action to ROLLED_BACK, which is no longer watched; and
# "rollback_error" means the job raised and will be delivered again.
_ROLLBACK_GAVE_UP = frozenset(
    {"rollback_refused", "rollback_refused_page_changed", "rollback_failed", "rollback_not_verified"}
)


def rollback_pending(managed: ManagedAction) -> bool:
    """The customer asked to undo the change and the job has not finished.

    The request only adds a history entry; the status stays the same until
    the job is done. Settling in between would record what a change the
    customer is taking back taught as if it had stayed, and a job that has
    already restored the site would lose its write to the settlement and go
    round again.
    """
    for event in reversed(managed.history):
        if event.reason == "rollback_requested":
            return True
        if event.reason in _ROLLBACK_GAVE_UP:
            return False
    return False


@dataclass(frozen=True)
class MeasurementWindows:
    change_day: date
    baseline_start: date
    baseline_end: date
    current_start: date
    current_end: date

    def compared_days(self) -> list[date]:
        return [
            *window_days(self.baseline_start, self.baseline_end),
            *window_days(self.current_start, self.current_end),
        ]


def measurement_windows(applied_at: datetime, *, window_days: int, settle_days: int) -> MeasurementWindows:
    change_day = search_console_today(applied_at)
    baseline_end = change_day - timedelta(days=1)
    current_start = change_day + timedelta(days=1 + settle_days)
    return MeasurementWindows(
        change_day=change_day,
        baseline_start=baseline_end - timedelta(days=window_days - 1),
        baseline_end=baseline_end,
        current_start=current_start,
        current_end=current_start + timedelta(days=window_days - 1),
    )


@dataclass
class MeasurementRoundReport:
    settled: int = 0
    waiting: int = 0
    skipped: int = 0
    failed: int = 0


class MeasurementRunner:
    def __init__(
        self,
        *,
        repository,
        action_store: ManagedActionStore,
        feedback_store: FeedbackStore,
        site_store: SiteStore,
        warehouse,
        transaction: Callable = nullcontext,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lag_days: int = GSC_DATA_LAG_DAYS,
        window_days: int = DEFAULT_WINDOW_DAYS,
        settle_days: int = DEFAULT_SETTLE_DAYS,
        deadline_days: int = DEFAULT_DEADLINE_DAYS,
    ) -> None:
        if window_days < 1 or settle_days < 0 or deadline_days < 0:
            raise ValueError("window_days must be >= 1; settle_days and deadline_days >= 0.")
        self._repository = repository
        self._actions = action_store
        self._feedback = feedback_store
        self._sites = site_store
        self._warehouse = warehouse
        self._transaction = transaction
        self._clock = clock
        self._lag_days = lag_days
        self._window_days = window_days
        self._settle_days = settle_days
        self._deadline_days = deadline_days

    def run_round(self) -> MeasurementRoundReport:
        report = MeasurementRoundReport()
        now = self._clock()
        newest_final = sync_window(now, lag_days=self._lag_days)[1]
        for record in self._repository.list(aggregate_type=ACTION_AGGREGATE_TYPE):
            managed = ManagedAction.model_validate(record.payload)
            if managed.status not in _WATCHED:
                continue
            try:
                if self.settle(managed, newest_final=newest_final, now=now):
                    report.settled += 1
                else:
                    report.waiting += 1
            except PersistenceConflictError:
                # Changed under us (a rollback, another server): next round
                # reads it again and sees what it has become.
                report.skipped += 1
            except Exception:
                log.exception("measurement: action %s could not be settled", managed.action_id)
                report.failed += 1
        if report.settled or report.failed:
            log.info(
                "measurement round: %d settled, %d waiting, %d skipped, %d failed",
                report.settled, report.waiting, report.skipped, report.failed,
            )
        return report

    def settle(self, managed: ManagedAction, *, newest_final: date, now: datetime) -> bool:
        """Settle one action if its data allows. True when it was settled."""
        policy = POLICIES.get(managed.action.action_type)
        if policy is None or managed.applied is None:
            log.warning(
                "measurement: action %s (%s) has nothing to measure it by",
                managed.action_id, managed.action.action_type.value,
            )
            return False
        if rollback_pending(managed):
            return False

        outcome = self._stored_outcome(managed.action_id)
        if outcome is None:
            windows = measurement_windows(
                managed.applied.applied_at, window_days=self._window_days, settle_days=self._settle_days
            )
            if newest_final < windows.current_end:
                return False
            ledger = self._warehouse.ledger(
                site_id=managed.site_id, start=windows.baseline_start, end=windows.current_end
            )
            # Only days from one property are compared: a day the warehouse
            # still holds from a property the site has since left is a
            # different source until it is fetched again, so it does not count.
            property_url = self._property_of(managed.site_id, ledger)
            own = [entry for entry in ledger if entry.property_url == property_url]
            stored = {entry.day for entry in own if entry.status.value == "synced"}
            complete = all(day in stored for day in windows.compared_days())
            if not complete and newest_final < windows.current_end + timedelta(days=self._deadline_days):
                return False
            capped = {entry.day for entry in own if entry.page_rows_capped}
            outcome = self._evaluate(
                managed, windows, policy, stored=stored, capped=capped, complete=complete, now=now
            )

        with self._transaction():
            self._save_outcome(managed, outcome)
            settled = self._actions.update(self._apply(managed, outcome))
            self._feedback.add(
                build_feedback_event(
                    feedback_id=feedback_id_for(managed.action_id),
                    action=settled.action,
                    outcome=outcome,
                ),
                tenant_id=managed.tenant_id,
            )
        return True

    # --- measuring ----------------------------------------------------------

    def _evaluate(
        self,
        managed: ManagedAction,
        windows: MeasurementWindows,
        policy: OutcomeEvaluationPolicy,
        *,
        stored: set[date],
        capped: set[date],
        complete: bool,
        now: datetime,
    ) -> OutcomeEvaluationResult:
        action = managed.action
        site_id, url, query = action.site_id, action.normalized_url, action.normalized_query
        start, end = windows.baseline_start, windows.current_end
        observations = build_observations(
            site_id=site_id,
            normalized_url=url,
            normalized_query=query,
            start=start,
            end=end,
            stored_days=stored,
            capped_days=capped,
            query_rows=self._warehouse.query_days(site_id=site_id, url=url, query=query, start=start, end=end),
            page_days=self._warehouse.page_days(site_id=site_id, url=url, start=start, end=end),
        )
        measurement = measure_action(
            measurement_id=f"measurement:{managed.action_id}",
            action_id=managed.action_id,
            site_id=site_id,
            normalized_url=url,
            normalized_query=query,
            baseline_observations=[o for o in observations if o.date <= windows.baseline_end],
            current_observations=[o for o in observations if o.date >= windows.current_start],
            baseline_start_date=windows.baseline_start,
            baseline_end_date=windows.baseline_end,
            current_start_date=windows.current_start,
            current_end_date=windows.current_end,
            snapshot=SnapshotMetadata(
                snapshot_id=f"measurement:{managed.action_id}",
                data_snapshot_id=f"warehouse:{site_id}:{start.isoformat()}:{end.isoformat()}",
                rule_version=action.snapshot.rule_version,
                config_version=CONFIG_VERSION,
                generated_at=now,
            ),
            evaluated_at=now,
        )
        outcome = evaluate_outcome(
            outcome_id=f"outcome:{managed.action_id}", measurement=measurement, policy=policy
        )
        return outcome.model_copy(
            update={
                "evidence": {
                    **outcome.evidence,
                    "change_day": windows.change_day.isoformat(),
                    "settle_days": self._settle_days,
                    "data_complete": complete,
                }
            }
        )

    def _property_of(self, site_id: str, ledger) -> str | None:
        """The site's Search Console property; once disconnected, the one its newest stored day came from."""
        try:
            site = self._sites.get(site_id)
        except PersistenceNotFoundError:
            site = None
        if site is not None and site.gsc is not None:
            return site.gsc.property_url
        synced = [entry for entry in ledger if entry.status.value == "synced"]
        return max(synced, key=lambda entry: entry.fetched_at).property_url if synced else None

    # --- recording ----------------------------------------------------------

    def _stored_outcome(self, action_id: str) -> OutcomeEvaluationResult | None:
        record = self._repository.get(aggregate_type=OUTCOME_AGGREGATE_TYPE, aggregate_id=action_id)
        return None if record is None else OutcomeEvaluationResult.model_validate(record.payload)

    def _save_outcome(self, managed: ManagedAction, outcome: OutcomeEvaluationResult) -> None:
        record = PersistenceRecord(
            record_id=f"outcome:{managed.action_id}:v1",
            aggregate_type=OUTCOME_AGGREGATE_TYPE,
            aggregate_id=managed.action_id,
            schema_version=OUTCOME_SCHEMA_VERSION,
            payload=outcome.model_dump(mode="json"),
            snapshot_id=outcome.snapshot.snapshot_id,
            data_snapshot_id=outcome.snapshot.data_snapshot_id,
            rule_version=outcome.snapshot.rule_version,
            config_version=outcome.snapshot.config_version,
            tenant_id=managed.tenant_id,
            site_id=managed.site_id,
        )
        try:
            self._repository.create(record)
        except PersistenceConflictError:
            existing = self._repository.get(aggregate_type=OUTCOME_AGGREGATE_TYPE, aggregate_id=managed.action_id)
            if existing is None or existing.payload != record.payload:
                raise

    @staticmethod
    def _apply(managed: ManagedAction, outcome: OutcomeEvaluationResult) -> ManagedAction:
        measurement = outcome.measurement
        if managed.status == ActionStatus.MEASUREMENT_WINDOW_ACTIVE:
            evaluating = mark_evaluating(action=managed.action, measurement=measurement)
            managed = managed.record(
                action=evaluating.action,
                actor=SYSTEM_ACTOR,
                reason=evaluating.reasons[0],
                detail=_measurement_detail(measurement, outcome),
            )
        settled = mark_outcome(action=managed.action, outcome=outcome)
        primary = outcome.primary_metric_outcome
        return managed.record(
            action=settled.action,
            actor=SYSTEM_ACTOR,
            reason=settled.reasons[0],
            detail={
                "outcome_id": outcome.outcome_id,
                "outcome_status": outcome.status.value,
                "primary_metric": outcome.primary_metric.value,
                "relative_change": primary.relative_change if primary is not None else None,
                "reasons": list(outcome.reasons),
            },
        )


def _measurement_detail(measurement: MeasurementResult, outcome: OutcomeEvaluationResult) -> dict:
    def window(summary):
        return {
            "start": summary.start_date.isoformat(),
            "end": summary.end_date.isoformat(),
            "impressions": summary.impressions,
            "clicks": summary.clicks,
            "ctr": summary.ctr,
            "median_position": summary.median_position,
            "completeness": summary.completeness,
        }

    return {
        "measurement_id": measurement.measurement_id,
        "measurement_status": measurement.status.value,
        "baseline": window(measurement.baseline),
        "current": window(measurement.current),
        "data_complete": outcome.evidence.get("data_complete"),
    }
