import json
from datetime import datetime, timezone

from app.models.llm import (
    LLMMessage,
)
from app.models.provider_config import (
    ArvanAIProviderConfig,
)
from app.models.reasoning import (
    TitleReasoningInput,
)
from app.models.snapshots import SnapshotMetadata
from app.reasoning.arvan_transport import (
    ArvanTransport,
)
from app.reasoning.provider import (
    StructuredTitleReasoner,
)


def make_input() -> TitleReasoningInput:
    return TitleReasoningInput(
        recommendation_id="recommendation-arvan-001",
        site_id="site-1",
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
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-arvan",
            data_snapshot_id="data-arvan",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime(
                2026,
                9,
                6,
                15,
                30,
                tzinfo=timezone.utc,
            ),
        ),
    )


class FakeArvanTransport:
    @property
    def provider_id(self) -> str:
        return "arvan_aiaas"

    def __init__(self):
        self.calls = 0
        self.messages = None

    def complete(
        self,
        *,
        messages: list[LLMMessage],
    ) -> str:
        self.calls += 1
        self.messages = messages

        return json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "candidate-1",
                        "title": (
                            "خرید کفش مردانه اصل"
                        ),
                        "rationale": (
                            "Preserves query relevance."
                        ),
                        "confidence_score": 0.91,
                    }
                ],
            },
            ensure_ascii=False,
        )


def test_arvan_transport_implements_generic_reasoner():
    transport = FakeArvanTransport()

    reasoner = StructuredTitleReasoner(
        transport=transport
    )

    candidates = (
        reasoner.generate_title_candidates(
            make_input()
        )
    )

    assert len(candidates) == 1
    assert candidates[0].title == (
        "خرید کفش مردانه اصل"
    )

    assert reasoner.provider_id == (
        "arvan_aiaas"
    )

    assert transport.calls == 1