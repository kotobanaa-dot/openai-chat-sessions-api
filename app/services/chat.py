"""Orchestration of one exchange: context -> model -> persistence -> cost."""

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import Settings
from app.core.errors import (
    SessionLimitError,
    SessionNotFoundError,
    UnknownModelPricingError,
    ValidationError,
)
from app.models.chat import ChatSession, Message
from app.providers.base import LLMProvider
from app.repositories.chat_repo import ChatRepository
from app.services.context_builder import ContextBuilder
from app.services.pricing import PricingService

logger = logging.getLogger(__name__)


@dataclass
class ResetResult:
    session: ChatSession
    messages_archived: int


@dataclass
class ExchangeResult:
    session: ChatSession
    user_message: Message
    assistant_message: Message
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    cost: Decimal
    session_total_cost: Decimal
    context_messages: int
    unpriced_usage: dict[str, int] | None
    # The model this exchange was actually priced by - it may differ from the
    # session default when the caller overrode it.
    model: str
    generation: int


class ChatService:
    def __init__(
        self,
        repo: ChatRepository,
        provider: LLMProvider,
        pricing: PricingService,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.provider = provider
        self.pricing = pricing
        self.settings = settings
        self.context_builder = ContextBuilder(settings.context_max_messages)

    async def create_session(
        self,
        model: str | None,
        title: str | None,
        system_prompt: str | None,
    ) -> ChatSession:
        chosen = model or self.settings.openai_model
        # Fail here rather than after a paid call: a model without a tariff
        # cannot be accounted for, so it must not be usable at all.
        try:
            self.pricing.price_for(chosen)
        except UnknownModelPricingError as exc:
            # The caller picked the model, so this is bad input (422), not a
            # server fault. A missing tariff for the server's own default
            # still surfaces as 500 - that one is a deployment error.
            if model is not None:
                raise ValidationError(
                    f"Model '{chosen}' is not supported by this service.",
                    details=exc.details,
                ) from exc
            raise
        return await self.repo.create_session(
            model=chosen, title=title, system_prompt=system_prompt
        )

    async def get_session_or_404(self, session_id: str, for_update: bool = False) -> ChatSession:
        session = await self.repo.get_session(session_id, for_update=for_update)
        if session is None:
            raise SessionNotFoundError(details={"session_id": session_id})
        return session

    async def reset_session(self, session_id: str) -> ResetResult:
        """Clear the active context while keeping the same session id.

        Earlier messages are archived under their generation rather than
        deleted - see ChatRepository.start_new_generation.
        """
        session = await self.get_session_or_404(session_id, for_update=True)
        archived = await self.repo.count_messages(session_id, generation=session.generation)

        # Resetting an untouched context would only create an empty generation.
        if archived == 0:
            return ResetResult(session=session, messages_archived=0)

        session = await self.repo.start_new_generation(session)
        return ResetResult(session=session, messages_archived=archived)

    async def send_message(
        self, session_id: str, content: str, model: str | None = None
    ) -> ExchangeResult:
        text = content.strip()
        if not text:
            raise ValidationError("Message content must not be empty.")
        if len(text) > self.settings.max_message_chars:
            raise ValidationError(
                f"Message is longer than {self.settings.max_message_chars} characters.",
                details={"length": len(text)},
            )

        session = await self.get_session_or_404(session_id, for_update=True)

        # Per-message model overrides the session default. Rejected here, before
        # anything is spent, if there is no tariff for it.
        chosen_model = model or session.model
        if model is not None:
            try:
                self.pricing.price_for(chosen_model)
            except UnknownModelPricingError as exc:
                raise ValidationError(
                    f"Model '{chosen_model}' is not supported by this service.",
                    details=exc.details,
                ) from exc

        generation = session.generation
        if (
            await self.repo.count_messages(session_id, generation=generation)
            >= self.settings.max_messages_per_session
        ):
            raise SessionLimitError(
                details={"limit": self.settings.max_messages_per_session}
            )

        history = await self.repo.list_messages(session_id, generation=generation)

        # Persist the user's message before calling the provider, and commit it:
        # without the commit, a provider failure would roll the request back and
        # the user's input would be lost. Committing here also releases the row
        # lock taken above, which is the trade-off - the model call can take
        # tens of seconds and holding a lock across it would serialise the
        # whole session behind one slow request.
        seq = await self.repo.next_seq(session_id)
        user_message = await self.repo.add_message(
            session_id=session_id,
            seq=seq,
            generation=generation,
            role="user",
            content=text,
        )
        await self.repo.commit()

        context = self.context_builder.build(session, history, text)
        reply = await self.provider.complete(context, model=chosen_model)

        usage = reply.usage
        # Priced by the model actually used, not by the session default.
        cost = self.pricing.calculate(
            model=chosen_model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
        )

        assistant_message = await self.repo.add_message(
            session_id=session_id,
            seq=seq + 1,
            generation=generation,
            role="assistant",
            content=reply.content,
            model=reply.model,
            finish_reason=reply.finish_reason,
            provider_response_id=reply.response_id,
        )

        await self.repo.add_usage(
            message_id=assistant_message.id,
            model=chosen_model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            total_tokens=usage.total_tokens,
            input_price_per_1m=cost.price.input,
            cached_input_price_per_1m=cost.price.cached_input,
            output_price_per_1m=cost.price.output,
            input_cost=cost.input_cost,
            output_cost=cost.output_cost,
            total_cost=cost.total_cost,
        )

        await self.repo.apply_usage_to_session(
            session,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost=cost.total_cost,
        )

        return ExchangeResult(
            session=session,
            user_message=user_message,
            assistant_message=assistant_message,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            total_tokens=usage.total_tokens,
            cost=cost.total_cost,
            session_total_cost=Decimal(session.total_cost),
            context_messages=len(context),
            unpriced_usage=usage.extra,
            model=chosen_model,
            generation=generation,
        )
