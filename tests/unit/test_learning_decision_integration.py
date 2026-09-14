from datetime import date, datetime, timezone

from app.engine.decision import run_decision_engine
from app.learning.engine import build_learning_context
from app.models.actions import ActionType
from app.models.evidence import DecisionEvidence
from app.models.learning import LearningMaturity
from app.models.observations import DataStatus, DailyObservation
from app.models.outcomes import OutcomeMetric, OutcomeStatus
from app.models.reconciliation import ReconciliationResult, ReconciliationStatus
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric
from app.models.strategies import StrategyType

from tests.unit.test_learning_engine import action, event


def snapshot():
    return SnapshotMetadata(
        snapshot_id="snap-stage21",
        data_snapshot_id="data-stage21",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )


def make_evidence() -> DecisionEvidence:
    baseline_day = date(2026, 8, 25)
    current_day = date(2026, 9, 5)

    baseline = DailyObservation(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=baseline_day,
        impressions=2000,
        clicks=120,
        avg_position=4.0,
        data_status=DataStatus.OBSERVED,
    )
    current = DailyObservation(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        date=current_day,
        impressions=2000,
        clicks=60,
        avg_position=7.0,
        data_status=DataStatus.OBSERVED,
    )

    return DecisionEvidence(
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        baseline_observations=[baseline],
        current_observations=[current],
        baseline_url_metrics=[
            URLDailyMetric(
                site_id="site-1",
                normalized_url="https://example.com/page",
                date=baseline_day,
                total_impressions=2000,
                total_clicks=120,
            )
        ],
        current_url_metrics=[
            URLDailyMetric(
                site_id="site-1",
                normalized_url="https://example.com/page",
                date=current_day,
                total_impressions=2000,
                total_clicks=60,
            )
        ],
        baseline_daily_status=[(baseline_day, DataStatus.OBSERVED)],
        current_daily_status=[(current_day, DataStatus.OBSERVED)],
        reconciliation=ReconciliationResult(
            impression_status=ReconciliationStatus.MATCH,
            click_status=ReconciliationStatus.MATCH,
            query_impressions=2000,
            url_impressions=2000,
            query_clicks=60,
            url_clicks=60,
            impression_gap=0,
            click_gap=0,
            is_valid=True,
            is_partial=False,
        ),
        snapshot=snapshot(),
    )


def established_positive_context():
    events = [event(str(index), OutcomeStatus.SUCCESS, 0.20) for index in range(1, 6)]
    return build_learning_context(
        context_id="learning-context-stage21",
        feedback_events=events,
        min_samples_for_learning=5,
    )


def established_negative_context():
    events = [event(str(index), OutcomeStatus.FAILURE, -0.20) for index in range(1, 6)]
    return build_learning_context(
        context_id="learning-context-stage21-negative",
        feedback_events=events,
        min_samples_for_learning=5,
    )


def test_learning_context_is_exposed_by_decision_engine():
    context = established_positive_context()

    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-context",
        learning_context=context,
    )

    assert result.learning_context == context


def test_established_positive_learning_adjusts_strategy_priority():
    context = established_positive_context()

    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-positive",
        learning_context=context,
    )

    assert result.strategy is not None
    assert result.action is not None
    assert result.strategy.strategy_type == StrategyType.SERP_TITLE_OPTIMIZATION
    assert result.action.action_type == ActionType.OPTIMIZE_TITLE
    assert result.strategy.priority_score > 0
    assert result.strategy.evidence["learning"]["applied"] is True
    assert result.strategy.evidence["learning"]["adjustment"] > 0
    assert result.action.priority_score == result.strategy.priority_score


def test_established_negative_learning_reduces_strategy_priority():
    context = established_negative_context()

    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-negative",
        learning_context=context,
    )

    assert result.strategy is not None
    assert result.strategy.evidence["learning"]["applied"] is True
    assert result.strategy.evidence["learning"]["adjustment"] < 0


def test_exploration_learning_does_not_adjust_strategy():
    context = build_learning_context(
        context_id="learning-context-exploration",
        feedback_events=[event("1", OutcomeStatus.SUCCESS, 0.20)],
        min_samples_for_learning=5,
    )

    assert context.signals[0].maturity == LearningMaturity.EXPLORATION

    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-exploration",
        learning_context=context,
    )

    assert result.strategy is not None
    learning = result.strategy.evidence["learning"]
    assert learning["applied"] is False
    assert learning["reason"] == "no_eligible_learning_signal"


def test_learning_does_not_change_classification_or_opportunity():
    base = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-invariance",
    )
    learned = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-invariance",
        learning_context=established_positive_context(),
    )

    assert learned.classification == base.classification
    assert learned.signals == base.signals
    assert learned.candidate == base.candidate
    assert learned.opportunity == base.opportunity


def test_learning_is_bounded():
    context = build_learning_context(
        context_id="learning-context-bounded",
        feedback_events=[
            event(str(index), OutcomeStatus.SUCCESS, 10.0)
            for index in range(1, 6)
        ],
        min_samples_for_learning=5,
    )

    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-bounded",
        learning_context=context,
    )

    assert result.strategy is not None
    adjustment = result.strategy.evidence["learning"]["adjustment"]
    assert adjustment == 0.10


def test_no_learning_context_preserves_baseline_priority():
    result = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-no-context",
    )

    assert result.strategy is not None
    learning = result.strategy.evidence["learning"]
    assert learning["applied"] is False
    assert learning["reason"] == "no_learning_context"


def test_learning_engine_remains_deterministic_inside_decision_engine():
    context = established_positive_context()

    first = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-deterministic",
        learning_context=context,
    )
    second = run_decision_engine(
        evidence=make_evidence(),
        candidate_id="stage21-deterministic",
        learning_context=context,
    )

    assert first.model_dump() == second.model_dump()
