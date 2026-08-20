"""Database access for sessions and messages.

Services talk to this class instead of writing queries inline, so the ordering
and locking rules live in one place.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatSession, Message, MessageUsage


class ChatRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- sessions ---

    async def create_session(
        self, model: str, title: str | None, system_prompt: str | None
    ) -> ChatSession:
        session = ChatSession(model=model, title=title, system_prompt=system_prompt)
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_session(self, session_id: str, for_update: bool = False) -> ChatSession | None:
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        if for_update:
            # Serialises concurrent sends into the same session so two
            # requests cannot claim the same seq. SQLite ignores this, which
            # is fine - tests are single-writer anyway.
            stmt = stmt.with_for_update()
        return await self.db.scalar(stmt)

    async def list_sessions(self, limit: int, offset: int) -> list[ChatSession]:
        stmt = (
            select(ChatSession)
            .order_by(ChatSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(await self.db.scalars(stmt))

    # --- messages ---

    async def list_messages(
        self, session_id: str, generation: int | None = None
    ) -> list[Message]:
        """Messages of one generation, or of the whole session when None.

        Callers that build context or render history pass the session's current
        generation, so messages archived by a reset stay out of the way.
        """
        stmt = select(Message).where(Message.session_id == session_id)
        if generation is not None:
            stmt = stmt.where(Message.generation == generation)
        return list(await self.db.scalars(stmt.order_by(Message.seq.asc())))

    async def next_seq(self, session_id: str) -> int:
        stmt = select(func.coalesce(func.max(Message.seq), 0)).where(
            Message.session_id == session_id
        )
        return int(await self.db.scalar(stmt) or 0) + 1

    async def count_messages(self, session_id: str, generation: int | None = None) -> int:
        stmt = select(func.count(Message.id)).where(Message.session_id == session_id)
        if generation is not None:
            stmt = stmt.where(Message.generation == generation)
        return int(await self.db.scalar(stmt) or 0)

    async def add_message(
        self,
        session_id: str,
        seq: int,
        role: str,
        content: str,
        generation: int = 1,
        model: str | None = None,
        status: str = "complete",
        finish_reason: str | None = None,
        provider_response_id: str | None = None,
    ) -> Message:
        message = Message(
            session_id=session_id,
            seq=seq,
            generation=generation,
            role=role,
            content=content,
            model=model,
            status=status,
            finish_reason=finish_reason,
            provider_response_id=provider_response_id,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    # --- usage / cost ---

    async def add_usage(self, **kwargs) -> MessageUsage:
        usage = MessageUsage(**kwargs)
        self.db.add(usage)
        await self.db.flush()
        return usage

    async def commit(self) -> None:
        """Explicit durability point.

        Used once per exchange, right after the user's message is written, so
        that a later provider failure cannot roll it away.
        """
        await self.db.commit()

    async def apply_usage_to_session(
        self,
        session: ChatSession,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost: Decimal,
    ) -> None:
        """Charge one exchange to both the active generation and the lifetime."""
        session.total_prompt_tokens += prompt_tokens
        session.total_completion_tokens += completion_tokens
        session.total_tokens += total_tokens
        session.total_cost = Decimal(session.total_cost) + cost

        session.lifetime_prompt_tokens += prompt_tokens
        session.lifetime_completion_tokens += completion_tokens
        session.lifetime_tokens += total_tokens
        session.lifetime_cost = Decimal(session.lifetime_cost) + cost
        await self.db.flush()

    async def start_new_generation(self, session: ChatSession) -> ChatSession:
        """Archive the current context by moving the session to a new generation.

        Nothing is deleted: earlier messages keep their generation number and
        their recorded cost. Only the active counters go back to zero, and the
        lifetime ones are deliberately left alone.
        """
        session.generation += 1
        session.total_prompt_tokens = 0
        session.total_completion_tokens = 0
        session.total_tokens = 0
        session.total_cost = Decimal("0")
        await self.db.flush()
        return session
