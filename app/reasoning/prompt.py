from app.models.reasoning import TitleReasoningInput
from app.models.llm import LLMMessage


SYSTEM_PROMPT = """
You are a strategic SEO reasoning component.

You must reason only from the evidence supplied by the application.

Rules:
- Do not invent SERP facts.
- Do not invent competitor data.
- Do not claim facts that are not present in the input.
- Do not copy competitor titles.
- Preserve primary query relevance.
- Return only valid JSON matching the requested schema.
- Generate title candidates only.
- Do not execute or imply execution of website changes.
""".strip()


def build_title_reasoning_prompt(
    reasoning_input: TitleReasoningInput,
) -> list[LLMMessage]:
    competitor_titles = "\n".join(
        f"- {title}"
        for title in reasoning_input.competitor_titles
    )

    user_prompt = f"""
Primary query:
{reasoning_input.primary_query}

Current title:
{reasoning_input.current_title or ""}

Target SERP position:
{reasoning_input.target_position}

Competitor titles:
{competitor_titles}

Competitor title median length:
{reasoning_input.competitor_title_length_median}

Competitor title average length:
{reasoning_input.competitor_title_length_average}

Current title length gap vs competitors:
{reasoning_input.title_length_gap_vs_competitors}

Constraints:
{chr(10).join(f"- {item}" for item in reasoning_input.constraints)}

Return JSON with exactly this structure:

{{
  "candidates": [
    {{
      "candidate_id": "candidate-1",
      "title": "string",
      "rationale": "string",
      "confidence_score": 0.0
    }}
  ]
}}
""".strip()

    return [
        LLMMessage(
            role="system",
            content=SYSTEM_PROMPT,
        ),
        LLMMessage(
            role="user",
            content=user_prompt,
        ),
    ]