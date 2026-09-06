from datetime import datetime, timezone
from app.models.candidates import CandidateStatus
import pytest

from app.classifier.candidate_builder import build_candidate
from app.models.classification import (
    ClassificationResult,
    ClassificationStatus,
)
from app.models.features import (
    CTRFeatures,
    DataQualityMatrix,
    FeatureSet,
    PositionFeatures,
    VisibilityFeatures,
    VolumeFeatures,
)
from app.models.signals import (
    Signal,
    SignalSeverity,
    SignalType,
)
from app.models.snapshots import SnapshotMetadata


def make_features() -> FeatureSet:
    return FeatureSet(
        volume=VolumeFeatures(
            current_impressions=2000,
            current_clicks=100,
            baseline_impressions=1800,
            baseline_clicks=110,
        ),
        ctr=CTRFeatures(
            baseline_ctr=0.061,
            current_ctr=0.05,
            absolute_delta=-0.011,
            relative_change=-0.18,
            baseline_ctr_zero=False,
        ),
        position=PositionFeatures(
            baseline_median=5.0,
            current_median=7.0,
            delta=2.0,
            mad_current=0.5,
            trend_slope=0.4,
            trend_direction="declining",
        ),
        visibility=VisibilityFeatures(
            query_impressions=2000,
            url_total_impressions=4000,
            query_visibility_share=0.5,
        ),
        data_quality=DataQualityMatrix(
            url_level_completeness=1.0,
            query_level_completeness=1.0,
            current_window_completeness=1.0,
            baseline_window_completeness=1.0,
        ),
    )


def make_signal(
    signal_type: SignalType,
    *,
    confidence: float = 0.9,
) -> Signal:
    return Signal(
        signal_type=signal_type,
        detected=True,
        severity=SignalSeverity.WARNING,
        confidence=confidence,
    )


def make_snapshot(
    snapshot_id: str = "snapshot-001",
    data_snapshot_id: str = "data-001",
    rule_version: str = "rules-v1",
    config_version: str = "config-v1",
) -> SnapshotMetadata:
    return SnapshotMetadata(
        snapshot_id=snapshot_id,
        data_snapshot_id=data_snapshot_id,
        rule_version=rule_version,
        config_version=config_version,
        generated_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )


def test_build_candidate():
    features = make_features()

    signals = [
        make_signal(
            SignalType.HIGH_VALUE_OPPORTUNITY,
            confidence=0.9,
        ),
        make_signal(
            SignalType.POSITION_DECLINE,
            confidence=0.8,
        ),
    ]

    classification = ClassificationResult(
        status=ClassificationStatus.INVESTIGATE,
        confidence=0.9,
        reasons=[
            "HIGH_VALUE_OPPORTUNITY",
            "POSITION_DECLINE",
        ],
        signal_count=2,
        risk_count=0,
    )

    snapshot = make_snapshot()

    candidate = build_candidate(
        candidate_id="candidate-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        features=features,
        signals=signals,
        classification=classification,
        snapshot=snapshot,
    )

    assert candidate.site_id == "site-1"
    assert candidate.normalized_url == "https://example.com/page"
    assert candidate.normalized_query == "کفش مردانه"

    assert candidate.status == ClassificationStatus.INVESTIGATE

    assert candidate.confidence == pytest.approx(0.9)

    assert candidate.priority_score == pytest.approx(3.6)

    assert candidate.signal_types == [
        "HIGH_VALUE_OPPORTUNITY",
        "POSITION_DECLINE",
    ]

    assert candidate.risk_types == []

    assert candidate.snapshot.snapshot_id == "snapshot-001"
    assert candidate.snapshot.data_snapshot_id == "data-001"
    assert candidate.snapshot.rule_version == "rules-v1"
    assert candidate.snapshot.config_version == "config-v1"


def test_risk_signal_is_recorded_separately():
    features = make_features()

    signals = [
        make_signal(
            SignalType.CTR_DROP,
            confidence=0.9,
        ),
        make_signal(
            SignalType.VOLATILITY,
            confidence=0.9,
        ),
        make_signal(
            SignalType.CTR_DROP_UNDER_VOLATILITY,
            confidence=0.8,
        ),
    ]

    classification = ClassificationResult(
        status=ClassificationStatus.INVESTIGATE,
        confidence=0.72,
        reasons=[
            "CTR_DROP",
            "CTR_DROP_UNDER_VOLATILITY",
        ],
        signal_count=3,
        risk_count=1,
    )

    snapshot = make_snapshot(
        snapshot_id="snapshot-002",
        data_snapshot_id="data-002",
    )

    candidate = build_candidate(
        candidate_id="candidate-002",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        features=features,
        signals=signals,
        classification=classification,
        snapshot=snapshot,
    )

    assert candidate.signal_types == [
        "CTR_DROP",
        "VOLATILITY",
        "CTR_DROP_UNDER_VOLATILITY",
    ]

    assert candidate.risk_types == [
        "CTR_DROP_UNDER_VOLATILITY",
    ]

    assert candidate.confidence == pytest.approx(0.72)

    assert candidate.snapshot.snapshot_id == "snapshot-002"
    assert candidate.snapshot.data_snapshot_id == "data-002"


def test_candidate_preserves_snapshot_metadata():
    features = make_features()

    signals = [
        make_signal(
            SignalType.CTR_DROP,
            confidence=0.9,
        ),
    ]

    classification = ClassificationResult(
        status=ClassificationStatus.INVESTIGATE,
        confidence=0.9,
        reasons=["CTR_DROP"],
        signal_count=1,
        risk_count=0,
    )

    snapshot = make_snapshot(
        snapshot_id="snapshot-replay-001",
        data_snapshot_id="data-replay-001",
        rule_version="rules-v7",
        config_version="config-v3",
    )

    candidate = build_candidate(
        candidate_id="candidate-replay-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        features=features,
        signals=signals,
        classification=classification,
        snapshot=snapshot,
    )

    assert candidate.snapshot.snapshot_id == "snapshot-replay-001"
    assert candidate.snapshot.data_snapshot_id == "data-replay-001"
    assert candidate.snapshot.rule_version == "rules-v7"
    assert candidate.snapshot.config_version == "config-v3"


def test_rejected_classification_produces_rejected_candidate():
    features = make_features()

    classification = ClassificationResult(
        status=ClassificationStatus.REJECT,
        confidence=0.0,
        reasons=["insufficient_data_quality"],
        signal_count=1,
        risk_count=0,
    )

    signals = [
        make_signal(
            SignalType.CTR_DROP,
            confidence=0.9,
        ),
    ]

    snapshot = make_snapshot(
        snapshot_id="snapshot-rejected-001",
        data_snapshot_id="data-rejected-001",
    )

    candidate = build_candidate(
        candidate_id="candidate-rejected-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        features=features,
        signals=signals,
        classification=classification,
        snapshot=snapshot,
    )

    assert candidate.status == CandidateStatus.REJECT
    assert candidate.confidence == pytest.approx(0.0)
    assert candidate.reasons == ["insufficient_data_quality"]