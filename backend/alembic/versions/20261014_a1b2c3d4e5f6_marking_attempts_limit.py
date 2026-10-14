"""marking: attempts limit for the current round

Ручной повтор после исчерпания попыток начинает новый круг из marking_max_attempts
попыток, а не одну попытку.

Revision ID: a1b2c3d4e5f6
Revises: 0065dea21d1f
Create Date: 2026-10-14 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "0065dea21d1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("marking_reports", sa.Column("attempts_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("marking_reports", "attempts_limit")
