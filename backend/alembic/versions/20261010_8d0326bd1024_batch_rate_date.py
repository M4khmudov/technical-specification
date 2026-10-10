"""batch rate date

Revision ID: 8d0326bd1024
Revises: ee7220b89c91
Create Date: 2026-10-10 16:55:20.565767
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8d0326bd1024"
down_revision: str | None = "ee7220b89c91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Для уже существующих партий считаем, что курс взят на дату поступления
    op.add_column("batches", sa.Column("rate_date", sa.Date(), nullable=True))
    op.execute("UPDATE batches SET rate_date = received_on")
    op.alter_column("batches", "rate_date", nullable=False)


def downgrade() -> None:
    op.drop_column("batches", "rate_date")
