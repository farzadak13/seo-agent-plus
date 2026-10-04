"""Does the configured Arvan AI model actually answer, and answer usably?

Two steps, so a failure says which half is broken:

  1. A one-line chat through the real transport. Fails here: endpoint, key,
     model name or network. Nothing about our prompt is involved yet.
  2. One full title proposal through the real reasoner, prompt and validator.
     Fails here: the model answers but not in the shape the product needs.

Reads the same settings the application does (SEO_AGENT_ARVAN_ENDPOINT,
SEO_AGENT_ARVAN_MODEL, SEO_AGENT_ARVAN_API_KEY_REF and the variable it
names), so a pass means the running service will work too. The key is never
printed.

    python scripts/probe_llm.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.llm import LLMMessage  # noqa: E402
from app.models.provider_config import ArvanAIProviderConfig  # noqa: E402
from app.models.reasoning import TitleReasoningInput  # noqa: E402
from app.models.snapshots import SnapshotMetadata  # noqa: E402
from app.reasoning.arvan_transport import ArvanTransport, ArvanTransportError  # noqa: E402
from app.reasoning.provider import StructuredTitleReasoner  # noqa: E402


def setting(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        sys.exit(f"{name} is not set.")
    return value


def main() -> int:
    endpoint = setting("SEO_AGENT_ARVAN_ENDPOINT")
    model = setting("SEO_AGENT_ARVAN_MODEL")
    key_ref = setting("SEO_AGENT_ARVAN_API_KEY_REF")
    api_key = setting(key_ref)
    timeout = float(os.environ.get("SEO_AGENT_LLM_TIMEOUT_SECONDS", "60"))

    print(f"endpoint: {endpoint}")
    print(f"model:    {model}")
    print(f"key:      ${key_ref}, {len(api_key)} characters")

    config = ArvanAIProviderConfig(
        endpoint=endpoint, api_key=api_key, model=model, timeout_seconds=timeout
    )
    transport = ArvanTransport(config=config)

    print("\n--- 1. one line through the transport ---")
    started = time.monotonic()
    try:
        reply = transport.complete(
            messages=[LLMMessage(role="user", content="فقط بنویس: سلام")]
        )
    except ArvanTransportError as exc:
        print(f"FAILED ({exc.failure_type.value}): {exc}")
        if exc.detail:
            print(f"Arvan said: {exc.detail}")
        return 1
    print(f"ok in {time.monotonic() - started:.1f}s: {reply.strip()[:80]!r}")
    print(f"usage: {transport.last_usage}")

    print("\n--- 2. a full title proposal ---")
    reasoner = StructuredTitleReasoner(transport=transport, max_retries=config.max_retries)
    started = time.monotonic()
    try:
        candidates = reasoner.generate_title_candidates(sample_input())
    except Exception as exc:  # the reasoner raises several kinds; all mean the same here
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(f"ok in {time.monotonic() - started:.1f}s, {len(candidates)} candidates:")
    for candidate in candidates:
        print(f"  {candidate.title}  ({len(candidate.title)} chars)")
    return 0 if candidates else 1


def sample_input() -> TitleReasoningInput:
    return TitleReasoningInput(
        recommendation_id="probe-llm",
        site_id="probe",
        normalized_url="https://example.com/men-shoes",
        primary_query="کفش مردانه",
        current_title="کفش مردانه",
        target_position=5,
        competitor_titles=[
            "خرید کفش مردانه با قیمت مناسب",
            "قیمت و خرید کفش مردانه اصل",
            "مدل‌های جدید کفش مردانه ۱۴۰۵",
        ],
        competitor_title_length_median=28.0,
        competitor_title_length_average=28.0,
        title_length_gap_vs_competitors=-18.0,
        confidence_score=1.0,
        constraints=["do_not_copy_competitor_titles", "preserve_primary_query_relevance"],
        evidence={"probe": True},
        snapshot=SnapshotMetadata(
            snapshot_id="probe-llm",
            data_snapshot_id="probe-llm",
            rule_version="probe",
            config_version="probe",
            generated_at=datetime.now(timezone.utc),
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
