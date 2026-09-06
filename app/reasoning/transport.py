from typing import Protocol

from app.models.llm import LLMMessage


class LLMTransport(Protocol):
    @property
    def provider_id(self) -> str:
        ...

    def complete(
        self,
        *,
        messages: list[LLMMessage],
    ) -> str:
        ...