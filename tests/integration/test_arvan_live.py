import os
from datetime import datetime, timezone

import pytest

from app.models.provider_config import (
    ArvanAIProviderConfig,
)
from app.models.reasoning import (
    TitleReasoningInput,
)
from app.models.snapshots import (
    SnapshotMetadata,
)
from app.reasoning.arvan_transport import (
    ArvanTransport,
)
from app.reasoning.provider import (
    StructuredTitleReasoner,
)


pytestmark = pytest.mark.integration


def test_arvan_live_structured_reasoning():
    endpoint = os.getenv(
        "ARVAN_AI_ENDPOINT"
    )

    api_key = os.getenv(
        "ARVAN_AI_API_KEY"
    )

    model = os.getenv(
        "ARVAN_AI_MODEL"
    )

    if not endpoint:
        pytest.skip(
            "ARVAN_AI_ENDPOINT is not configured."
        )

    if not api_key:
        pytest.skip(
            "ARVAN_AI_API_KEY is not configured."
        )

    if not model:
        pytest.skip(
            "ARVAN_AI_MODEL is not configured."
        )

    reasoning_input = TitleReasoningInput(
        recommendation_id="live-arvan-001",
        site_id="live-test",
        normalized_url="https://example.com/page",
        primary_query="کفش مردانه",
        current_title="عنوان فعلی",
        target_position=5,
        competitor_titles=[
            "خرید کفش مردانه",
            "قیمت کفش مردانه",
            "بهترین کفش مردانه",
        ],
        competitor_title_length_median=20.0,
        competitor_title_length_average=20.0,
        title_length_gap_vs_competitors=-10.0,
        confidence_score=1.0,
        constraints=[
            "do_not_copy_competitor_titles",
            "preserve_primary_query_relevance",
        ],
        evidence={
            "test": True,
        },
        snapshot=SnapshotMetadata(
            snapshot_id="live-arvan",
            data_snapshot_id="live-arvan",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime.now(
                timezone.utc
            ),
        ),
    )

    config = ArvanAIProviderConfig(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
    )

    transport = ArvanTransport(
        config=config
    )

    reasoner = StructuredTitleReasoner(
        transport=transport,
        max_retries=config.max_retries,
    )

    candidates = (
        reasoner.generate_title_candidates(
            reasoning_input
        )
    )

    assert isinstance(candidates, list)
    assert len(candidates) >= 1