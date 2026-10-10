"""user memories

Revision ID: 552d4d6a3f3b
Revises: 069e28669928
Create Date: 2026-10-10 18:48:05.662148
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "552d4d6a3f3b"
down_revision: str | None = "069e28669928"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("normalized", sa.String(length=300), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_user_memories_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_memories_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_memories")),
        sa.UniqueConstraint("user_id", "normalized", name=op.f("uq_user_memories_user_id")),
    )
    op.create_index(op.f("ix_user_memories_user_id"), "user_memories", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_memories_user_id"), table_name="user_memories")
    op.drop_table("user_memories")
