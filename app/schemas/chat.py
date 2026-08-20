"""Request and response schemas.

Pydantic handles the first line of input validation (types, lengths, required
fields); business rules that need the database live in the services.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Amounts are rendered with a fixed 8 decimals, matching NUMERIC(18, 8) in the
# database. Without this, Decimal("0") serialises as "0E-8" - technically the
# same number, but unreadable for something that represents money.
Money = Annotated[
    Decimal,
    PlainSerializer(lambda v: f"{Decimal(v):.8f}", return_type=str, when_used="json"),
]


class SessionCreate(BaseModel):
    model: str | None = Field(
        default=None, description="Model id. Falls back to the server default."
    )
    title: str | None = Field(default=None, max_length=200)
    system_prompt: str | None = Field(default=None, max_length=4000)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    model: str | None = Field(
        default=None,
        description=(
            "Model to answer this message with. Falls back to the session's "
            "model. The cost is charged at the tariff of the model used."
        ),
    )


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    seq: int
    role: str
    content: str
    model: str | None
    status: str = Field(
        description="'complete', or 'failed' when the model returned no usable text."
    )
    created_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None
    model: str
    system_prompt: str | None
    status: str

    generation: int = Field(
        description="Increments on every reset. The active context is this generation."
    )

    # Active context - reset puts these back to zero.
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: Money

    # Everything the session ever spent, across all generations. Never reset.
    lifetime_tokens: int
    lifetime_cost: Money

    created_at: datetime
    updated_at: datetime


class SessionDetailOut(SessionOut):
    # Only the active generation: messages archived by a reset are not part of
    # the conversation any more.
    messages: list[MessageOut]
    currency: str = "USD"


class ResetOut(BaseModel):
    session_id: str
    generation: int = Field(description="Generation number after the reset.")
    messages_archived: int = Field(
        description="Messages moved out of the active context by this reset."
    )
    total_cost: Money = Field(description="Cost of the active context - zero after a reset.")
    lifetime_cost: Money = Field(description="What the session has spent in total.")
    currency: str = "USD"


class SendMessageOut(BaseModel):
    session_id: str
    user_message: MessageOut
    assistant_message: MessageOut
    model: str = Field(description="Model that answered and was charged for.")
    generation: int
    usage: UsageOut
    cost: Money = Field(description="Cost of this exchange, USD.")
    total_accumulated_cost: Money = Field(description="Cost of the whole session, USD.")
    currency: str = "USD"
    context_messages: int = Field(
        description="How many messages were sent to the model as context."
    )
    unpriced_usage: dict[str, int] | None = Field(
        default=None,
        description="Usage categories reported by the provider but not priced separately.",
    )


class ModelOut(BaseModel):
    model: str
    input_per_1m: Decimal
    cached_input_per_1m: Decimal
    output_per_1m: Decimal
    currency: str
