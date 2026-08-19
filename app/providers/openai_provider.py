"""OpenAI adapter.

Everything SDK-specific lives here: client setup, retries and the mapping of
provider failures onto this service's domain errors.
"""

import logging

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from app.core.config import Settings
from app.core.errors import UpstreamRejectedError, UpstreamUnavailableError
from app.providers.base import ProviderReply, ProviderUsage
from app.services.context_builder import ChatMessage

logger = logging.getLogger(__name__)


class OpenAIProvider:
    def __init__(self, settings: Settings) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
            # The SDK retries 429 and 5xx with exponential backoff itself -
            # no reason to hand-roll a retry loop on top of it.
            max_retries=settings.openai_max_retries,
        )
        self._max_output_tokens = settings.openai_max_output_tokens

    async def complete(self, messages: list[ChatMessage], model: str) -> ProviderReply:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[m.as_dict() for m in messages],
                max_completion_tokens=self._max_output_tokens,
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            # Transient: retrying later can succeed.
            logger.warning("openai temporarily unavailable: %s", type(exc).__name__)
            raise UpstreamUnavailableError(
                "The model provider is temporarily unavailable, try again shortly."
            ) from exc
        except (AuthenticationError, PermissionDeniedError) as exc:
            # Configuration problem - retrying would just burn time.
            logger.error("openai rejected credentials: %s", type(exc).__name__)
            raise UpstreamRejectedError(
                "The model provider rejected the API credentials."
            ) from exc
        except (BadRequestError, NotFoundError) as exc:
            # Only the status code is logged: the SDK's exception text can echo
            # the request body back, which would put user messages in the logs.
            logger.error(
                "openai rejected the request: %s (status %s)",
                type(exc).__name__,
                getattr(exc, "status_code", "unknown"),
            )
            raise UpstreamRejectedError(
                "The model provider rejected the request (check the model name)."
            ) from exc
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise UpstreamUnavailableError() from exc
            raise UpstreamRejectedError() from exc

        choice = response.choices[0]
        content = choice.message.content or ""

        usage = response.usage
        cached = 0
        extra: dict[str, int] = {}
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
            out_details = getattr(usage, "completion_tokens_details", None)
            reasoning = getattr(out_details, "reasoning_tokens", 0) or 0
            if reasoning:
                # Billed as output by OpenAI and already inside
                # completion_tokens; recorded here only for transparency.
                extra["reasoning_tokens"] = reasoning

        return ProviderReply(
            content=content,
            model=response.model,
            finish_reason=choice.finish_reason,
            response_id=response.id,
            usage=ProviderUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                cached_tokens=cached,
                extra=extra or None,
            ),
        )
