"""chat files and code runs

Revision ID: 069e28669928
Revises: 747af1b59306
Create Date: 2026-10-10 18:32:53.303155
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "069e28669928"
down_revision: str | None = "747af1b59306"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "origin",
            sa.Enum(
                "upload",
                "generated",
                name="file_origin",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_chat_files_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("fk_chat_files_message_id_chat_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_chat_files_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_files")),
    )
    op.create_index(
        op.f("ix_chat_files_conversation_id"), "chat_files", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_chat_files_message_id"), "chat_files", ["message_id"], unique=False)
    op.add_column(
        "chat_messages",
        sa.Column(
            "code_runs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "code_runs")
    op.drop_index(op.f("ix_chat_files_message_id"), table_name="chat_files")
    op.drop_index(op.f("ix_chat_files_conversation_id"), table_name="chat_files")
    op.drop_table("chat_files")
