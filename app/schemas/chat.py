"""Request and response schemas.

Pydantic handles the first line of input validation (types, lengths, required
fields); business rules that need the database live in the services.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    model: str | None = Field(
        default=None, description="Model id. Falls back to the server default."
    )
    title: str | None = Field(default=None, max_length=200)
    system_prompt: str | None = Field(default=None, max_length=4000)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


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
    created_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None
    model: str
    system_prompt: str | None
    status: str
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: Decimal
    created_at: datetime
    updated_at: datetime


class SessionDetailOut(SessionOut):
    messages: list[MessageOut]
    currency: str = "USD"


class SendMessageOut(BaseModel):
    session_id: str
    user_message: MessageOut
    assistant_message: MessageOut
    usage: UsageOut
    cost: Decimal = Field(description="Cost of this exchange, USD.")
    total_accumulated_cost: Decimal = Field(description="Cost of the whole session, USD.")
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
