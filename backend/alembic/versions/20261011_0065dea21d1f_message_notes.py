"""message notes

Revision ID: 0065dea21d1f
Revises: 552d4d6a3f3b
Create Date: 2026-10-11 01:11:00.487395
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0065dea21d1f"
down_revision: str | None = "552d4d6a3f3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "notes", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "notes")
