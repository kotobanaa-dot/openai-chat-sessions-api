"""initial schema: sessions, messages, message_usage

Revision ID: 0001
Revises:
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 8)


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("total_prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_cost", MONEY, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="complete"),
        sa.Column("finish_reason", sa.String(50), nullable=True),
        sa.Column("provider_response_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_messages_session_seq"),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])

    op.create_table(
        "message_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_price_per_1m", MONEY, nullable=False),
        sa.Column("cached_input_price_per_1m", MONEY, nullable=False),
        sa.Column("output_price_per_1m", MONEY, nullable=False),
        sa.Column("input_cost", MONEY, nullable=False),
        sa.Column("output_cost", MONEY, nullable=False),
        sa.Column("total_cost", MONEY, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("message_usage")
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_table("messages")
    op.drop_table("sessions")
