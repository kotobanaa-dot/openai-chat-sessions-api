"""session generations and lifetime totals

Reset starts a new generation rather than deleting history: the active context
becomes empty and its totals go back to zero, while the spending that already
happened stays recorded in the lifetime columns.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 8)


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
    )
    for column in ("lifetime_prompt_tokens", "lifetime_completion_tokens", "lifetime_tokens"):
        op.add_column(
            "sessions",
            sa.Column(column, sa.BigInteger(), nullable=False, server_default="0"),
        )
    op.add_column(
        "sessions",
        sa.Column("lifetime_cost", MONEY, nullable=False, server_default="0"),
    )

    op.add_column(
        "messages",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_messages_generation", "messages", ["generation"])

    # Existing sessions have spent everything inside their first generation, so
    # lifetime starts equal to the current totals rather than at zero.
    op.execute(
        """
        UPDATE sessions
           SET lifetime_prompt_tokens = total_prompt_tokens,
               lifetime_completion_tokens = total_completion_tokens,
               lifetime_tokens = total_tokens,
               lifetime_cost = total_cost
        """
    )


def downgrade() -> None:
    op.drop_index("ix_messages_generation", table_name="messages")
    op.drop_column("messages", "generation")
    op.drop_column("sessions", "lifetime_cost")
    op.drop_column("sessions", "lifetime_tokens")
    op.drop_column("sessions", "lifetime_completion_tokens")
    op.drop_column("sessions", "lifetime_prompt_tokens")
    op.drop_column("sessions", "generation")
