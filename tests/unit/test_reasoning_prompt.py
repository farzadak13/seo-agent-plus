from datetime import datetime, timezone

from app.models.reasoning import TitleReasoningInput
from app.models.snapshots import SnapshotMetadata
from app.reasoning.prompt import (
    SYSTEM_PROMPT,
    build_title_reasoning_prompt,
)


def make_input() -> TitleReasoningInput:
    return TitleReasoningInput(
        recommendation_id="recommendation-prompt-001",
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
        competitor_title_length_average=21.0,
        title_length_gap_vs_competitors=-10.0,
        confidence_score=0.9,
        constraints=[
            "do_not_copy_competitor_titles",
            "preserve_primary_query_relevance",
        ],
        evidence={
            "competitor_count": 2,
        },
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-prompt",
            data_snapshot_id="data-prompt",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime(
                2026,
                9,
                6,
                14,
                0,
                tzinfo=timezone.utc,
            ),
        ),
    )


def test_prompt_has_system_and_user_messages():
    messages = build_title_reasoning_prompt(
        make_input()
    )

    assert len(messages) == 2
    assert messages[0].role == "system"
    assert messages[1].role == "user"


def test_system_prompt_forbids_invented_evidence():
    assert "Do not invent SERP facts." in SYSTEM_PROMPT
    assert "Do not invent competitor data." in SYSTEM_PROMPT


def test_user_prompt_contains_primary_query():
    messages = build_title_reasoning_prompt(
        make_input()
    )

    assert "کفش مردانه" in messages[1].content


def test_user_prompt_contains_current_title():
    messages = build_title_reasoning_prompt(
        make_input()
    )

    assert "عنوان فعلی" in messages[1].content


def test_user_prompt_contains_competitor_titles():
    messages = build_title_reasoning_prompt(
        make_input()
    )

    assert "خرید کفش مردانه" in messages[1].content
    assert "قیمت کفش مردانه" in messages[1].content


def test_user_prompt_contains_constraints():
    messages = build_title_reasoning_prompt(
        make_input()
    )

    assert (
        "do_not_copy_competitor_titles"
        in messages[1].content
    )

    assert (
        "preserve_primary_query_relevance"
        in messages[1].content
    )


def test_prompt_requires_structured_json():
    messages = build_title_reasoning_prompt(
        make_input()
    )

    assert '"candidates"' in messages[1].content
    assert '"candidate_id"' in messages[1].content
    assert '"confidence_score"' in messages[1].content


def test_prompt_is_deterministic():
    first = build_title_reasoning_prompt(
        make_input()
    )

    second = build_title_reasoning_prompt(
        make_input()
    )

    assert [
        message.model_dump()
        for message in first
    ] == [
        message.model_dump()
        for message in second
    ]