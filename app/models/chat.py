"""ORM models: sessions, messages and per-message usage.

Money is stored as Numeric (never float): a chat session accumulates thousands
of tiny amounts, and binary floating point drifts away from the provider's
invoice once those are summed.

Types stay portable on purpose - String instead of a native Postgres ENUM, so
the same models also run on SQLite for tests.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Money: 18 digits total, 8 after the point. A single call can cost a few
# millionths of a dollar, so 8 decimals keeps per-call precision.
MONEY = Numeric(18, 8)
PRICE = Numeric(18, 8)


def _uuid() -> str:
    return str(uuid.uuid4())


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    # Reset starts a new generation instead of deleting anything. Messages from
    # earlier generations stay in the table but are no longer part of the
    # active conversation.
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Totals of the ACTIVE generation - reset puts them back to zero, which is
    # what a caller sees after resetting the session.
    total_prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)

    # Totals across every generation. Never reset: the money was really spent,
    # and a service whose purpose is cost accounting must not lose that.
    lifetime_prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    lifetime_completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    lifetime_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    lifetime_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.seq",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Guarantees a stable order and blocks two concurrent requests from
        # writing the same position in the dialogue.
        UniqueConstraint("session_id", "seq", name="uq_messages_session_seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # Which generation this message belongs to. seq keeps counting across
    # resets, so the uniqueness rule above needs no change.
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="complete", nullable=False)
    finish_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_response_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
    usage: Mapped["MessageUsage | None"] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class MessageUsage(Base):
    """Token usage and cost of one assistant reply.

    Kept in its own table rather than as columns on messages: usage exists only
    for assistant rows, and this keeps cost reporting independent of the
    dialogue itself.
    """

    __tablename__ = "message_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Price snapshot: a later tariff change must not rewrite past cost.
    input_price_per_1m: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    cached_input_price_per_1m: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    output_price_per_1m: Mapped[Decimal] = mapped_column(PRICE, nullable=False)

    input_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    output_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    message: Mapped["Message"] = relationship(back_populates="usage")
