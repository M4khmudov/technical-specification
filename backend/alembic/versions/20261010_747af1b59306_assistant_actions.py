"""assistant actions

Revision ID: 747af1b59306
Revises: 3caccff7da33
Create Date: 2026-10-10 18:12:08.276846
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "747af1b59306"
down_revision: str | None = "3caccff7da33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "confirmed",
                "rejected",
                "expired",
                "failed",
                name="action_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_assistant_actions_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("fk_assistant_actions_message_id_chat_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_assistant_actions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_actions")),
    )
    op.create_index(
        op.f("ix_assistant_actions_conversation_id"),
        "assistant_actions",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assistant_actions_user_id"), "assistant_actions", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_assistant_actions_user_id"), table_name="assistant_actions")
    op.drop_index(op.f("ix_assistant_actions_conversation_id"), table_name="assistant_actions")
    op.drop_table("assistant_actions")
