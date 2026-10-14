"""Демо-данные: всё, что требует ТЗ, на месте, и повторный запуск ничего не дублирует."""

from sqlalchemy import func, select

from app.core import clock
from app.core.security import verify_password
from app.models import Batch, Material, ProductionJob, Recipe, User
from app.models.enums import BatchStatus, Currency, JobStatus, Role
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
    assert {JobStatus.MARKED, JobStatus.IN_PROGRESS, JobStatus.PLANNED, JobStatus.DRAFT} <= statuses
    history = [j for j in jobs if j.status == JobStatus.MARKED]
    assert len(history) >= 20
    assert min(j.completed_at.date() for j in history) < clock.today().replace(day=1)

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
