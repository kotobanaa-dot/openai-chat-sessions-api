"""Response schemas for the spending report.

The repository returns sums; the derived numbers (averages, cost per 1000
characters) are assembled here, where an empty database is a normal case
rather than a division by zero.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from app.repositories.stats_repo import (
    DayStats,
    FailedStats,
    ModelStats,
    OverallStats,
)
from app.schemas.chat import Money

_QUANT = Decimal("0.00000001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


class OverallOut(BaseModel):
    sessions: int
    replies: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: Money

    @classmethod
    def of(cls, stats: OverallStats) -> "OverallOut":
        return cls(
            sessions=stats.sessions,
            replies=stats.replies,
            prompt_tokens=stats.prompt_tokens,
            cached_tokens=stats.cached_tokens,
            completion_tokens=stats.completion_tokens,
            total_tokens=stats.total_tokens,
            total_cost=_money(stats.total_cost),
        )


class ModelStatOut(BaseModel):
    model: str
    replies: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    total_cost: Money

    avg_cost_per_reply: Money
    avg_output_tokens: int = Field(
        description="Output tokens per reply, including tokens spent on reasoning."
    )
    answer_chars: int = Field(description="Characters of answer text this model produced.")
    cost_per_1k_chars: Money | None = Field(
        default=None,
        description=(
            "What 1000 characters of delivered answer cost with this model. "
            "Null when the model produced no text at all. This is the number "
            "that separates a cheap tariff from a cheap answer: reasoning "
            "tokens are billed as output but never reach the reader."
        ),
    )

    @classmethod
    def of(cls, stats: ModelStats) -> "ModelStatOut":
        replies = stats.replies or 1  # only reached when a model has no rows
        per_1k = (
            _money(stats.total_cost * 1000 / Decimal(stats.answer_chars))
            if stats.answer_chars
            else None
        )
        return cls(
            model=stats.model,
            replies=stats.replies,
            prompt_tokens=stats.prompt_tokens,
            cached_tokens=stats.cached_tokens,
            completion_tokens=stats.completion_tokens,
            total_cost=_money(stats.total_cost),
            avg_cost_per_reply=_money(stats.total_cost / replies),
            avg_output_tokens=round(stats.completion_tokens / replies),
            answer_chars=stats.answer_chars,
            cost_per_1k_chars=per_1k,
        )


class DayOut(BaseModel):
    day: date
    replies: int
    total_cost: Money

    @classmethod
    def of(cls, stats: DayStats) -> "DayOut":
        return cls(day=stats.day, replies=stats.replies, total_cost=_money(stats.total_cost))


class FailedOut(BaseModel):
    replies: int = Field(description="Billed replies that carried no usable text.")
    total_cost: Money = Field(description="Money spent on those replies.")

    @classmethod
    def of(cls, stats: FailedStats) -> "FailedOut":
        return cls(replies=stats.replies, total_cost=_money(stats.total_cost))


class StatsOut(BaseModel):
    overall: OverallOut
    by_model: list[ModelStatOut]
    by_day: list[DayOut]
    failed: FailedOut
    currency: str = "USD"
