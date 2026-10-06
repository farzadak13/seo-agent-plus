"""scripts/probe_llm.py: what the operator sees when the model answers in the wrong shape."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "probe_llm.py"
KEY = "sk-test-this-key-must-never-be-printed"


@pytest.fixture
def probe(monkeypatch):
    spec = importlib.util.spec_from_file_location("probe_llm", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("SEO_AGENT_ARVAN_ENDPOINT", "https://arvan.example/v1")
    monkeypatch.setenv("SEO_AGENT_ARVAN_MODEL", "some-model")
    monkeypatch.setenv("SEO_AGENT_ARVAN_API_KEY_REF", "ARVAN_AI_KEY")
    monkeypatch.setenv("ARVAN_AI_KEY", KEY)
    return module


def answering(*answers):
    class FakeArvan:
        last_usage = {"total_tokens": 1}
        last_response_metadata = {}

        def __init__(self, *, config):
            self._answers = list(answers)

        def complete(self, *, messages):
            return self._answers.pop(0)

    return FakeArvan


def test_a_wrong_shape_shows_what_the_model_said_and_never_the_key(probe, monkeypatch, capsys):
    monkeypatch.setattr(probe, "ArvanTransport", answering("سلام", "Here you go: not json"))

    assert probe.main() == 1

    out = capsys.readouterr().out
    assert "FAILED: ReasoningProviderError: LLM returned invalid JSON." in out
    assert "the model answered" in out and "Here you go: not json" in out
    assert KEY not in out


def test_a_fenced_answer_passes(probe, monkeypatch, capsys):
    fenced = '```json\n{"candidates": [{"candidate_id": "candidate-1", "title": "خرید کفش مردانه اصل", ' \
             '"rationale": "keeps the query", "confidence_score": 0.9}]}\n```'
    monkeypatch.setattr(probe, "ArvanTransport", answering("سلام", fenced))

    assert probe.main() == 0
    assert "1 candidates" in capsys.readouterr().out
