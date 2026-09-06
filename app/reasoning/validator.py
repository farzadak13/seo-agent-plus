from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)


def _normalize(value: str) -> str:
    return " ".join(value.split()).strip()


def validate_title_candidate(
    *,
    candidate: TitleReasoningCandidate,
    reasoning_input: TitleReasoningInput,
) -> list[str]:
    errors: list[str] = []

    title = _normalize(candidate.title)
    query = _normalize(reasoning_input.primary_query)

    if not title:
        errors.append("empty_title")

    if not query:
        errors.append("empty_primary_query")

    if len(title) > 200:
        errors.append("title_too_long")

    if title.lower() == (
        _normalize(
            reasoning_input.current_title or ""
        ).lower()
    ):
        errors.append("title_identical_to_current")

    normalized_title = title.casefold()
    normalized_query = query.casefold()

    if normalized_query not in normalized_title:
        errors.append(
            "primary_query_not_present_in_title"
        )

    competitor_titles = {
        _normalize(value).casefold()
        for value in reasoning_input.competitor_titles
    }

    if normalized_title in competitor_titles:
        errors.append(
            "title_matches_competitor_title"
        )

    return errors