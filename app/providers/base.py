"""Provider contract.

The rest of the app depends on this protocol, not on the OpenAI SDK, so the
provider can be swapped or faked in tests without touching business logic.
"""

from dataclasses import dataclass
from typing import Protocol

from app.services.context_builder import ChatMessage


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    # Categories reported by the provider that this service does not price
    # separately (e.g. reasoning tokens). Kept for transparency.
    extra: dict[str, int] | None = None


@dataclass(frozen=True)
class ProviderReply:
    content: str
    model: str
    usage: ProviderUsage
    finish_reason: str | None = None
    response_id: str | None = None

    @property
    def is_empty(self) -> bool:
        """No visible text came back, even though tokens were billed.

        Happens when a reasoning model spends the whole output budget thinking.
        """
        return not self.content.strip()


class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        max_output_tokens: int,
    ) -> ProviderReply: ...
