"""Aggregated spending, read from the usage rows the service already writes.

Nothing here is stored. Every number is derived from message_usage on demand,
so the report can never drift away from the per-message accounting that the
rest of the service is built on.

Averages and ratios are computed in Python rather than in SQL: an empty
database makes SQL return NULL for AVG and raises on division, and the
expressions that avoid that stop being portable between PostgreSQL and the
SQLite used by the tests.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatSession, Message, MessageUsage


@dataclass(frozen=True)
class OverallStats:
    sessions: int
    replies: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: Decimal


@dataclass(frozen=True)
class ModelStats:
    model: str
    replies: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    total_cost: Decimal
    # Characters of visible answer text produced by this model. The point of
    # comparison: tokens are what you pay for, characters are what you get.
    answer_chars: int


@dataclass(frozen=True)
class DayStats:
    day: date
    replies: int
    total_cost: Decimal


@dataclass(frozen=True)
class FailedStats:
    """Replies that were billed but carried no usable text.

    A reasoning model can spend its whole output budget thinking and return an
    empty message; the provider still charges for those tokens. Money spent on
    nothing deserves its own line rather than being averaged into the rest.
    """

    replies: int
    total_cost: Decimal


class StatsRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def overall(self) -> OverallStats:
        sessions = await self.db.scalar(select(func.count(ChatSession.id))) or 0
        row = (
            await self.db.execute(
                select(
                    func.count(MessageUsage.id),
                    func.coalesce(func.sum(MessageUsage.prompt_tokens), 0),
                    func.coalesce(func.sum(MessageUsage.cached_tokens), 0),
                    func.coalesce(func.sum(MessageUsage.completion_tokens), 0),
                    func.coalesce(func.sum(MessageUsage.total_tokens), 0),
                    func.coalesce(func.sum(MessageUsage.total_cost), 0),
                )
            )
        ).one()
        return OverallStats(
            sessions=sessions,
            replies=row[0],
            prompt_tokens=row[1],
            cached_tokens=row[2],
            completion_tokens=row[3],
            total_tokens=row[4],
            total_cost=Decimal(row[5]),
        )

    async def by_model(self) -> list[ModelStats]:
        """One row per model that was actually used.

        Joined to messages for the answer length: the usage row knows the
        price, the message knows what was delivered for it.
        """
        stmt = (
            select(
                MessageUsage.model,
                func.count(MessageUsage.id),
                func.coalesce(func.sum(MessageUsage.prompt_tokens), 0),
                func.coalesce(func.sum(MessageUsage.cached_tokens), 0),
                func.coalesce(func.sum(MessageUsage.completion_tokens), 0),
                func.coalesce(func.sum(MessageUsage.total_cost), 0),
                func.coalesce(func.sum(func.length(Message.content)), 0),
            )
            .join(Message, Message.id == MessageUsage.message_id)
            .group_by(MessageUsage.model)
            .order_by(func.sum(MessageUsage.total_cost).desc())
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            ModelStats(
                model=row[0],
                replies=row[1],
                prompt_tokens=row[2],
                cached_tokens=row[3],
                completion_tokens=row[4],
                total_cost=Decimal(row[5]),
                answer_chars=row[6],
            )
            for row in rows
        ]

    async def by_day(self, limit: int = 14) -> list[DayStats]:
        """Daily spending, oldest first, capped to the most recent `limit` days.

        func.date works on both PostgreSQL and SQLite, which keeps the same
        query under test as in production.
        """
        day = func.date(MessageUsage.created_at).label("day")
        stmt = (
            select(
                day,
                func.count(MessageUsage.id),
                func.coalesce(func.sum(MessageUsage.total_cost), 0),
            )
            .group_by(day)
            .order_by(day.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            DayStats(day=_as_date(row[0]), replies=row[1], total_cost=Decimal(row[2]))
            for row in reversed(rows)
        ]

    async def failed(self) -> FailedStats:
        stmt = (
            select(
                func.count(MessageUsage.id),
                func.coalesce(func.sum(MessageUsage.total_cost), 0),
            )
            .join(Message, Message.id == MessageUsage.message_id)
            .where(Message.status == "failed")
        )
        row = (await self.db.execute(stmt)).one()
        return FailedStats(replies=row[0], total_cost=Decimal(row[1]))


def _as_date(value: object) -> date:
    """PostgreSQL returns a date object here, SQLite returns 'YYYY-MM-DD'."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
