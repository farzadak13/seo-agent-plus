from datetime import datetime, timezone

from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)
from app.models.snapshots import SnapshotMetadata
from app.reasoning.validator import (
    validate_title_candidate,
)


def make_input() -> TitleReasoningInput:
    return TitleReasoningInput(
        recommendation_id="recommendation-validator-001",
        site_id="site-1",
        normalized_url="https://example.com/page",
        primary_query="کفش مردانه",
        current_title="عنوان فعلی",
        target_position=5,
        competitor_titles=[
            "خرید کفش مردانه",
            "قیمت کفش مردانه",
        ],
        competitor_title_length_median=20.0,
        competitor_title_length_average=20.0,
        title_length_gap_vs_competitors=-8.0,
        confidence_score=1.0,
        constraints=[
            "do_not_copy_competitor_titles",
        ],
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-validator",
            data_snapshot_id="data-validator",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime(
                2026,
                9,
                6,
                9,
                0,
                tzinfo=timezone.utc,
            ),
        ),
    )


def test_valid_title_has_no_errors():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-1",
        title="خرید کفش مردانه اصل",
        rationale="relevant",
        confidence_score=0.9,
    )

    errors = validate_title_candidate(
        candidate=candidate,
        reasoning_input=make_input(),
    )

    assert errors == []


def test_missing_query_is_rejected():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-2",
        title="عنوان خوب",
        rationale="test",
        confidence_score=0.9,
    )

    reasoning_input = make_input()
    reasoning_input.primary_query = ""

    errors = validate_title_candidate(
        candidate=candidate,
        reasoning_input=reasoning_input,
    )

    assert "empty_primary_query" in errors


def test_query_must_be_present():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-3",
        title="عنوان بدون عبارت هدف",
        rationale="test",
        confidence_score=0.9,
    )

    errors = validate_title_candidate(
        candidate=candidate,
        reasoning_input=make_input(),
    )

    assert (
        "primary_query_not_present_in_title"
        in errors
    )


def test_current_title_is_rejected():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-4",
        title="عنوان فعلی",
        rationale="test",
        confidence_score=0.9,
    )

    errors = validate_title_candidate(
        candidate=candidate,
        reasoning_input=make_input(),
    )

    assert "title_identical_to_current" in errors


def test_competitor_clone_is_rejected():
    candidate = TitleReasoningCandidate(
        candidate_id="candidate-5",
        title="خرید کفش مردانه",
        rationale="copy competitor",
        confidence_score=0.9,
    )

    errors = validate_title_candidate(
        candidate=candidate,
        reasoning_input=make_input(),
    )

    assert "title_matches_competitor_title" in errors