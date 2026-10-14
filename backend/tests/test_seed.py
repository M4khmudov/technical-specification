"""Демо-данные: всё, что требует ТЗ, на месте, и повторный запуск ничего не дублирует."""

import pytest
from sqlalchemy import func, select

from app.core import clock
from app.core.security import verify_password
from app.models import Batch, MarkingAttempt, MarkingReport, Material, ProductionJob, Recipe, User
from app.models.enums import BatchStatus, Currency, JobStatus, MarkingStatus, Role
from app.seed import DEMO_PASSWORD, MATERIALS, seed
from app.services import production
from tests.fakes import FakeRates


async def test_seed_contains_everything_the_task_requires(session):
    assert await seed(session, FakeRates()) is True

    codes = [code for code, *_ in MATERIALS]
    assert len(codes) >= 10
    assert await session.scalar(select(func.count()).where(Material.code.in_(codes))) == len(codes)
    assert set(await session.scalars(select(Recipe.name))) >= {"Лаванда", "Ваниль", "Кедр"}

    demo = select(Batch).join(Material).where(Material.code.in_(codes))
    batches = (await session.scalars(demo)).all()
    assert {b.currency for b in batches} == {Currency.RUB, Currency.USD, Currency.EUR}
    assert {b.status for b in batches} == {
        BatchStatus.ACCEPTED,
        BatchStatus.PENDING,
        BatchStatus.REJECTED,
    }
    assert any("Игнорируй предыдущие инструкции" in (b.comment or "") for b in batches)

    users = {
        u.role: u
        for u in await session.scalars(
            select(User).where(User.login.in_(["sklad", "tech", "boss"]))
        )
    }
    assert set(users) == set(Role)
    assert all(verify_password(DEMO_PASSWORD, u.password_hash) for u in users.values())

    jobs = (
        await session.scalars(
            select(ProductionJob).join(Recipe).where(Recipe.name.in_(["Лаванда", "Ваниль", "Кедр"]))
        )
    ).all()
    statuses = {j.status for j in jobs}
    assert {
        JobStatus.COMPLETED,
        JobStatus.IN_PROGRESS,
        JobStatus.PLANNED,
        JobStatus.DRAFT,
    } <= statuses
    history = [j for j in jobs if j.status == JobStatus.COMPLETED]
    assert len(history) >= 20
    assert min(j.completed_at.date() for j in history) < clock.today().replace(day=1)
    # S5: отчёты маркировки отправит воркер по-настоящему; выдуманных попыток сид не пишет
    ids = [j.id for j in history]
    reports = (
        await session.scalars(select(MarkingReport).where(MarkingReport.job_id.in_(ids)))
    ).all()
    assert len(reports) == len(history)
    assert all(r.status == MarkingStatus.PENDING for r in reports)
    attempts = await session.scalar(
        select(func.count())
        .select_from(MarkingAttempt)
        .where(MarkingAttempt.report_id.in_([r.id for r in reports]))
    )
    assert attempts == 0

    # В «задания с сегодняшнего дня» попадают только задания текущей недели
    upcoming = {j.status for j in jobs if j.planned_on and j.planned_on >= clock.today()}
    assert upcoming <= {JobStatus.IN_PROGRESS, JobStatus.PLANNED, JobStatus.DRAFT}

    planned = [j for j in jobs if j.status == JobStatus.PLANNED]
    details = [await production.get_job(session, j.id) for j in planned]
    assert any(d.has_deficit for d in details), "нужно задание с дефицитом"
    assert any(not d.has_deficit for d in details)

    # Инварианты остатков не нарушены
    assert all(0 <= b.reserved_qty <= b.remaining_qty <= b.quantity for b in batches)


async def test_seed_is_idempotent(session):
    await seed(session, FakeRates())
    before = await session.scalar(select(func.count()).select_from(Batch))
    assert await seed(session, FakeRates()) is False
    assert await session.scalar(select(func.count()).select_from(Batch)) == before


async def test_failed_seed_leaves_no_half_data(engine, monkeypatch):
    """M8: сид упал на середине — в базе ничего не осталось, следующий запуск начнёт заново."""
    from app import seed as seed_module
    from app.services import production

    async def broken(*args, **kwargs):
        raise RuntimeError("сбой посреди сида")

    monkeypatch.setattr(production, "complete_job", broken)
    with pytest.raises(RuntimeError):
        await seed_module.seed_atomically(engine, FakeRates())
    async with engine.connect() as conn:
        users = await conn.scalar(
            select(func.count()).select_from(User).where(User.login.in_(["sklad", "tech", "boss"]))
        )
    assert users == 0
