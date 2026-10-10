"""Схема БД: миграции совпадают с моделями, инварианты закреплены на уровне базы."""

from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401
from app.db.base import Base
from app.models import Batch
from tests.factories import make_batch, make_material


async def test_migrations_match_models(engine):
    def diff(sync_conn):
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_quantities_are_exact_decimals(session):
    material = await make_material(session)
    batch = await make_batch(session, material, qty="0.1")
    batch.quantity = batch.remaining_qty = batch.remaining_qty + Decimal("0.2")
    await session.flush()
    await session.refresh(batch)
    assert isinstance(batch.remaining_qty, Decimal)
    assert batch.remaining_qty == Decimal("0.3")  # с float было бы 0.30000000000000004


async def test_batch_numbers_are_assigned_automatically(session):
    material = await make_material(session)
    first = await make_batch(session, material)
    second = await make_batch(session, material)
    assert second.number > first.number


@pytest.mark.parametrize(
    "values",
    [
        {"reserved_qty": Decimal("11")},  # резерв больше остатка
        {"remaining_qty": Decimal("-1")},  # отрицательный остаток
        {"reserved_qty": Decimal("-1")},  # отрицательный резерв
    ],
)
async def test_batch_stock_invariants_enforced_by_db(session, values):
    material = await make_material(session)
    batch = await make_batch(session, material, qty="10")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(update(Batch).where(Batch.id == batch.id).values(**values))


async def test_unknown_status_rejected_by_db(session):
    material = await make_material(session)
    batch = await make_batch(session, material)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE batches SET status = 'lost' WHERE id = :id"), {"id": batch.id}
            )
