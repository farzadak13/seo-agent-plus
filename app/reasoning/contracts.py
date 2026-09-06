from typing import Protocol

from app.models.reasoning import (
    TitleReasoningCandidate,
    TitleReasoningInput,
)


class StrategicReasoner(Protocol):
    @property
    def provider_id(self) -> str:
        ...

    def generate_title_candidates(
        self,
        reasoning_input: TitleReasoningInput,
    ) -> list[TitleReasoningCandidate]:
        ...