"""Suggest the words each page should be found by. The model proposes; nothing here decides.

The answer is checked line by line before anything uses it: every page must be
one the scan read, every word is normalised the way stored queries are, and a
word the model gives twice, or for a page it was not shown, is dropped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.llm import LLMMessage
from app.models.target_keywords import KeywordIntent
from app.normalization.query import normalize_query
from app.reasoning.provider import unwrap_json
from app.reasoning.transport import LLMTransport

PROMPT_VERSION = "target-keywords-v1"
MAX_PAGES = 40
MAX_KEYWORDS = 20
PER_PAGE = 3
MAX_ANSWER_ITEMS = 60

SYSTEM_PROMPT = """
You plan the search words for a Persian-language website that is too new to
have search data of its own.

Rules:
- Suggest words people in Iran would actually type into Google, in Persian, as
  they type them: short, plain, no punctuation.
- Each word belongs to ONE of the pages given, the one whose title and heading
  fit it best. Use the page address exactly as given.
- Prefer specific words a new site can win over broad ones it cannot.
- Do not invent search volumes, rankings, brands of other businesses or facts
  that are not in the input.
- Return only valid JSON matching the requested schema.
""".strip()


class KeywordSuggestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)
    intent: KeywordIntent
    reason: str = Field(default="", max_length=300)


class KeywordSuggestionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Checked one by one below: a single word too long must not cost the rest.
    keywords: list[dict]


@dataclass(frozen=True)
class PageForKeywords:
    url: str
    title: str | None
    h1: str | None


@dataclass(frozen=True)
class KeywordSuggestion:
    keyword: str
    url: str
    intent: KeywordIntent
    reason: str


class KeywordSuggestionError(RuntimeError):
    pass


def build_keyword_prompt(*, description: str, seeds: list[str], pages: list[PageForKeywords]) -> list[LLMMessage]:
    listed = "\n".join(
        f"- {page.url} | title: {page.title or ''} | heading: {page.h1 or ''}" for page in pages[:MAX_PAGES]
    )
    user = f"""
What the business does, in the owner's words:
{description.strip() or "(not given)"}

Words the owner already has in mind:
{", ".join(seeds) or "(none)"}

The site's pages:
{listed}

Give at most {MAX_KEYWORDS} words in all and at most {PER_PAGE} for one page, the most
useful first.

Return JSON with exactly this structure:

{{
  "keywords": [
    {{"keyword": "string", "url": "one of the page addresses above", "intent": "transactional | commercial | informational | navigational", "reason": "one short sentence in Persian"}}
  ]
}}
""".strip()
    return [LLMMessage(role="system", content=SYSTEM_PROMPT), LLMMessage(role="user", content=user)]


def parse_keyword_answer(raw: str, *, pages: list[PageForKeywords], identity) -> list[KeywordSuggestion]:
    """The usable suggestions in an answer. Raises when the answer is not the schema at all."""
    try:
        payload = KeywordSuggestionList.model_validate(json.loads(unwrap_json(raw)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise KeywordSuggestionError(f"The model's answer was not the expected JSON: {type(exc).__name__}") from exc
    known = {identity(page.url): page.url for page in pages[:MAX_PAGES]}
    seen: set[str] = set()
    per_page: dict[str, int] = {}
    kept: list[KeywordSuggestion] = []
    for raw_item in payload.keywords[:MAX_ANSWER_ITEMS]:
        try:
            item = KeywordSuggestionPayload.model_validate(raw_item)
        except ValidationError:
            continue
        keyword = normalize_query(item.keyword)
        url = known.get(identity(item.url))
        if not keyword or len(keyword) > 80 or url is None or keyword in seen:
            continue
        if per_page.get(url, 0) >= PER_PAGE or len(kept) >= MAX_KEYWORDS:
            continue
        seen.add(keyword)
        per_page[url] = per_page.get(url, 0) + 1
        kept.append(KeywordSuggestion(keyword=keyword, url=url, intent=item.intent, reason=" ".join(item.reason.split())[:300]))
    return kept


class KeywordSuggester:
    """One model call, retried once on an answer that is not the schema."""

    def __init__(self, *, transport: LLMTransport, attempts: int = 2) -> None:
        self._transport = transport
        self._attempts = attempts

    @property
    def provider_id(self) -> str:
        return self._transport.provider_id

    def suggest(self, *, description: str, seeds: list[str], pages: list[PageForKeywords], identity) -> list[KeywordSuggestion]:
        if not pages:
            return []
        messages = build_keyword_prompt(description=description, seeds=seeds, pages=pages)
        last: Exception | None = None
        for _ in range(self._attempts):
            try:
                return parse_keyword_answer(self._transport.complete(messages=messages), pages=pages, identity=identity)
            except KeywordSuggestionError as exc:
                last = exc
        raise KeywordSuggestionError(str(last))
