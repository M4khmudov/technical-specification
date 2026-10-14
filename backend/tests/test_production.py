"""Задания и резервы: FIFO, частичный резерв, дефицит, запуск, завершение, отмена."""

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core import clock
from app.core.errors import ConflictError, DomainError
from app.models import Batch, BatchMovement, Material, Recipe, User
from app.models.enums import BatchStatus, JobStatus, MovementKind
from app.schemas.production import ActualIn, JobCompleteIn, JobCreateIn, Light
from app.services import production as service
from tests.factories import make_batch, make_material, make_recipe, make_user

D = Decimal
PLANNED_ON = clock.today() + timedelta(days=3)


@dataclass
class Shop:
    user: User
    wax: Material
    wick: Material
    recipe: Recipe  # «Лаванда»: 0,2 кг воска и 1 фитиль на свечу


async def make_shop(session) -> Shop:
    user = await make_user(session)
    wax = await make_material(session, name="Воск соевый")
    wick = await make_material(session, name="Фитиль")
    wick.unit = "шт"
    recipe = await make_recipe(session, {wax: "0.2", wick: "1"})
    return Shop(user, wax, wick, recipe)


def job_in(shop: Shop, quantity: int) -> JobCreateIn:
    return JobCreateIn(recipe_id=shop.recipe.id, quantity=quantity, planned_on=PLANNED_ON)


async def batch(session, material, qty, day: int, status=BatchStatus.ACCEPTED, price="100"):
    return await make_batch(
        session, material, qty, received_on=date(2026, 9, day), status=status, price=price
    )


def by_material(job, material):
    return next(m for m in job.materials if m.material_id == material.id)


async def reload(session, b: Batch) -> Batch:
    await session.refresh(b)
    return b


# ---------------------------------------------------------------- FIFO и частичный резерв


async def test_fifo_reserves_oldest_batches_first(session):
    shop = await make_shop(session)
    late = await batch(session, shop.wax, "10", day=3)
    first = await batch(session, shop.wax, "5", day=1)
    middle = await batch(session, shop.wax, "3", day=2)
    await batch(session, shop.wick, "100", day=1)

    job = await service.create_job(session, shop.user, job_in(shop, 40), plan=True)  # 8 кг

    wax_res = [(r.batch_id, r.quantity) for r in job.reservations if r.material_id == shop.wax.id]
    assert wax_res == [(first.id, D("5")), (middle.id, D("3"))]
    assert (await reload(session, late)).reserved_qty == 0
    assert job.status == JobStatus.PLANNED
    assert {m.light for m in job.materials} == {Light.GREEN}


async def test_pending_and_rejected_batches_are_not_used(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "100", day=1, status=BatchStatus.PENDING)
    await batch(session, shop.wax, "100", day=1, status=BatchStatus.REJECTED)
    good = await batch(session, shop.wax, "1", day=5)

    job = await service.create_job(session, shop.user, job_in(shop, 10), plan=True)  # 2 кг

    assert [r.batch_id for r in job.reservations] == [good.id]
    assert by_material(job, shop.wax).deficit_qty == D("1")


async def test_shortage_gives_partial_reserve_deficit_and_traffic_light(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "6", day=1)  # нужно 8 кг — жёлтый
    # фитилей нет совсем — красный

    job = await service.create_job(session, shop.user, job_in(shop, 40), plan=True)

    wax, wick = by_material(job, shop.wax), by_material(job, shop.wick)
    assert (wax.required_qty, wax.reserved_qty, wax.deficit_qty) == (D("8"), D("6"), D("2"))
    assert (wick.reserved_qty, wick.deficit_qty) == (D("0"), D("40"))
    assert (wax.light, wick.light) == (Light.YELLOW, Light.RED)
    assert job.has_deficit
    assert not job.cost_covers_all


async def test_draft_reserves_nothing(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "100", day=1)
    job = await service.create_job(session, shop.user, job_in(shop, 10))
    assert job.status == JobStatus.DRAFT
    assert job.reservations == []
    # M1: черновик не «красный», если на складе хватает: светофор — по свободному остатку
    assert by_material(job, shop.wax).light == Light.GREEN
    assert by_material(job, shop.wick).light == Light.RED  # фитилей на складе нет
    assert job.has_deficit and job.cost_basis == "stock_average"
    assert job.cost_rub == Decimal("200.00")  # 10 × 0,2 кг × 100 ₽ (фитилей нет — без них)


async def test_job_in_the_past_rejected(session):
    shop = await make_shop(session)
    data = JobCreateIn(
        recipe_id=shop.recipe.id, quantity=1, planned_on=clock.today() - timedelta(days=1)
    )
    with pytest.raises(DomainError, match="в прошлом"):
        await service.create_job(session, shop.user, data)


# ---------------------------------------------------------------- запуск


async def test_cannot_start_with_deficit_and_message_says_what_is_missing(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "5.5", day=1)
    await batch(session, shop.wick, "100", day=1)
    job = await service.create_job(session, shop.user, job_in(shop, 40), plan=True)

    with pytest.raises(DomainError, match=r"не хватает 2,5\s+кг «Воск соевый»"):
        await service.start_job(session, shop.user, job.id)
    assert (await service.get_job(session, job.id)).status == JobStatus.PLANNED


async def test_rereserve_after_new_batch_then_start(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "5", day=1)
    await batch(session, shop.wick, "100", day=1)
    job = await service.create_job(session, shop.user, job_in(shop, 40), plan=True)
    assert by_material(job, shop.wax).deficit_qty == D("3")

    await batch(session, shop.wax, "10", day=20)  # пришла и принята новая партия
    job = await service.reserve_job(session, shop.user, job.id)
    assert not job.has_deficit

    job = await service.start_job(session, shop.user, job.id)
    assert job.status == JobStatus.IN_PROGRESS
    assert job.started_at is not None


@pytest.mark.parametrize(
    ("action", "status_text"),
    [("start_job", "«Черновик»"), ("reserve_job", "«Черновик»")],
)
async def test_wrong_status_transitions(session, action, status_text):
    shop = await make_shop(session)
    job = await service.create_job(session, shop.user, job_in(shop, 1))
    with pytest.raises(ConflictError, match=status_text):
        await getattr(service, action)(session, shop.user, job.id)


# ---------------------------------------------------------------- завершение


async def started_job(session, shop: Shop, quantity: int = 40):
    job = await service.create_job(session, shop.user, job_in(shop, quantity), plan=True)
    return await service.start_job(session, shop.user, job.id)


async def test_complete_with_savings_returns_rest_to_batches(session):
    shop = await make_shop(session)
    b1 = await batch(session, shop.wax, "5", day=1)
    b2 = await batch(session, shop.wax, "10", day=2)
    await batch(session, shop.wick, "100", day=1)
    job = await started_job(session, shop)  # резерв 8 кг: 5 из b1 + 3 из b2

    job = await service.complete_job(
        session,
        shop.user,
        job.id,
        JobCompleteIn(actuals=[ActualIn(material_id=shop.wax.id, actual_qty=D("7"))]),
    )

    assert job.status == JobStatus.COMPLETED
    assert by_material(job, shop.wax).actual_qty == D("7")
    assert by_material(job, shop.wick).actual_qty == D("40")  # не указан — по норме
    b1, b2 = await reload(session, b1), await reload(session, b2)
    assert (b1.remaining_qty, b1.reserved_qty) == (D("0"), D("0"))
    assert (b2.remaining_qty, b2.reserved_qty) == (D("8"), D("0"))  # 1 кг экономии вернулся
    assert job.reservations == []

    kinds = (
        await session.execute(
            select(BatchMovement.kind, BatchMovement.quantity).where(
                BatchMovement.batch_id == b2.id
            )
        )
    ).all()
    assert (MovementKind.CONSUME, D("2")) in kinds
    assert (MovementKind.RELEASE, D("1")) in kinds


async def test_complete_with_overrun_takes_extra_from_free_stock_fifo(session):
    shop = await make_shop(session)
    b1 = await batch(session, shop.wax, "8", day=1)
    b2 = await batch(session, shop.wax, "10", day=2)
    await batch(session, shop.wick, "100", day=1)
    job = await started_job(session, shop)  # 8 кг из b1

    await service.complete_job(
        session,
        shop.user,
        job.id,
        JobCompleteIn(actuals=[ActualIn(material_id=shop.wax.id, actual_qty=D("9.5"))]),
    )

    b1, b2 = await reload(session, b1), await reload(session, b2)
    assert b1.remaining_qty == D("0")
    assert (b2.remaining_qty, b2.reserved_qty) == (D("8.5"), D("0"))


async def test_overrun_not_covered_by_stock_changes_nothing(session):
    shop = await make_shop(session)
    b1 = await batch(session, shop.wax, "8.5", day=1)
    await batch(session, shop.wick, "100", day=1)
    job = await started_job(session, shop)

    with pytest.raises(DomainError, match=r"не хватает 1,5\s+кг «Воск соевый»"):
        await service.complete_job(
            session,
            shop.user,
            job.id,
            JobCompleteIn(actuals=[ActualIn(material_id=shop.wax.id, actual_qty=D("10"))]),
        )

    b1 = await reload(session, b1)
    assert (b1.remaining_qty, b1.reserved_qty) == (D("8.5"), D("8"))
    assert (await service.get_job(session, job.id)).status == JobStatus.IN_PROGRESS


async def test_completion_rejects_foreign_material(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "8", day=1)
    await batch(session, shop.wick, "100", day=1)
    other = await make_material(session)
    job = await started_job(session, shop)
    with pytest.raises(DomainError, match="не входит в задание"):
        await service.complete_job(
            session,
            shop.user,
            job.id,
            JobCompleteIn(actuals=[ActualIn(material_id=other.id, actual_qty=D("1"))]),
        )


async def test_cost_estimate_then_actual(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "5", day=1, price="200")
    await batch(session, shop.wax, "10", day=2, price="300")
    await batch(session, shop.wick, "100", day=1, price="2.5")

    job = await service.create_job(session, shop.user, job_in(shop, 40), plan=True)
    # 5×200 + 3×300 + 40×2,5 = 2000
    assert job.cost_rub == D("2000.00")
    assert not job.cost_is_final

    job = await service.start_job(session, shop.user, job.id)
    job = await service.complete_job(
        session,
        shop.user,
        job.id,
        JobCompleteIn(actuals=[ActualIn(material_id=shop.wax.id, actual_qty=D("9"))]),
    )
    # 5×200 + 4×300 + 40×2,5 = 2300
    assert job.cost_rub == D("2300.00")
    assert job.cost_is_final


# ---------------------------------------------------------------- отмена


async def test_cancel_planned_releases_reservations(session):
    shop = await make_shop(session)
    b1 = await batch(session, shop.wax, "10", day=1)
    job = await service.create_job(session, shop.user, job_in(shop, 40), plan=True)
    assert (await reload(session, b1)).reserved_qty == D("8")

    job = await service.cancel_job(session, shop.user, job.id)

    assert job.status == JobStatus.CANCELLED
    assert (await reload(session, b1)).reserved_qty == D("0")
    assert job.reservations == []


async def test_cancel_draft(session):
    shop = await make_shop(session)
    job = await service.create_job(session, shop.user, job_in(shop, 1))
    assert (await service.cancel_job(session, shop.user, job.id)).status == JobStatus.CANCELLED


async def test_cannot_cancel_after_start(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "10", day=1)
    await batch(session, shop.wick, "100", day=1)
    job = await started_job(session, shop)
    with pytest.raises(ConflictError, match="отменить можно только"):
        await service.cancel_job(session, shop.user, job.id)


async def test_list_marks_jobs_with_deficit(session):
    shop = await make_shop(session)
    await batch(session, shop.wax, "10", day=1)
    await batch(session, shop.wick, "10", day=1)
    fine = await service.create_job(session, shop.user, job_in(shop, 5), plan=True)
    short = await service.create_job(session, shop.user, job_in(shop, 40), plan=True)

    page = await service.list_jobs(session, status=JobStatus.PLANNED, planned_from=PLANNED_ON)
    flags = {j.id: j.has_deficit for j in page.items}
    assert flags[short.id] is True
    assert flags[fine.id] is False


# ---------------------------------------------------------------- конкурентность


async def committed_shop(session_factory, wax_qty: str) -> tuple[Shop, Batch]:
    async with session_factory() as s:
        tag = uuid4().hex[:6]
        user = await make_user(s, login=f"race-{tag}")
        wax = await make_material(s, code=f"RACE-{tag}", name="Воск гонка")
        wick = await make_material(s, code=f"RACE-W-{tag}", name="Фитиль гонка")
        recipe = await make_recipe(s, {wax: "1"}, name=f"Гонка {tag}")
        stock = await make_batch(s, wax, wax_qty, received_on=date(2026, 9, 1))
        await s.commit()
        return Shop(user, wax, wick, recipe), stock


async def test_two_concurrent_plans_cannot_reserve_same_stock(session_factory):
    shop, stock = await committed_shop(session_factory, "10")

    async def create_and_plan():
        async with session_factory() as s:
            job = await service.create_job(s, shop.user, job_in(shop, 8))
            return await service.plan_job(s, shop.user, job.id)

    jobs = await asyncio.gather(create_and_plan(), create_and_plan())

    reserved = sorted(by_material(j, shop.wax).reserved_qty for j in jobs)
    assert reserved == [D("2"), D("8")]  # одному всё, второму остаток, а не 8 + 8
    async with session_factory() as s:
        batch_now = await s.get(Batch, stock.id)
        assert batch_now.reserved_qty == D("10")


async def test_many_concurrent_plans_never_oversell(session_factory):
    shop, stock = await committed_shop(session_factory, "10")

    async def create_and_plan():
        async with session_factory() as s:
            job = await service.create_job(s, shop.user, job_in(shop, 3))
            return await service.plan_job(s, shop.user, job.id)

    jobs = await asyncio.gather(*(create_and_plan() for _ in range(6)))

    total = sum((by_material(j, shop.wax).reserved_qty for j in jobs), D("0"))
    assert total == D("10")  # 6 × 3 = 18 запрошено, отдано ровно сколько было
    async with session_factory() as s:
        assert (await s.get(Batch, stock.id)).reserved_qty == D("10")


async def test_job_cost_total_is_the_sum_of_its_lines(session):
    """M6: итог себестоимости равен сумме строк разбивки (раньше расходился на копейку)."""
    from app.services import analytics

    shop = await make_shop(session)
    await batch(session, shop.wax, "0.1", day=1, price="0.04")  # 0,1 × 0,04 = 0,004 → 0,00
    await batch(session, shop.wax, "0.1", day=2, price="0.04")
    await batch(session, shop.wick, "100", day=1, price="0.004")  # сумма без округления 0,012
    job = await service.create_job(session, shop.user, job_in(shop, 1), plan=True)
    report = await analytics.job_cost_breakdown(session, job.number)
    assert report.job.cost_rub == sum((line.amount for line in report.lines), D("0"))


async def test_pieces_must_be_whole(session):
    """M2: 0,5 фитиля не бывает — ни в факте расхода, ни в поступлении."""
    shop = await make_shop(session)
    await batch(session, shop.wax, "10", day=1)
    await batch(session, shop.wick, "100", day=1)
    job = await started_job(session, shop)
    with pytest.raises(DomainError, match="в штуках"):
        await service.complete_job(
            session,
            shop.user,
            job.id,
            JobCompleteIn(actuals=[ActualIn(material_id=shop.wick.id, actual_qty=D("40.5"))]),
        )


async def test_audit_is_readable(session):
    """M7: в журнале — изделие по имени и числа без «1E+3» и хвостовых нулей."""
    from app.models import AuditLog

    shop = await make_shop(session)
    await batch(session, shop.wax, "1000", day=1)
    await batch(session, shop.wick, "5000", day=1)
    job = await service.create_job(session, shop.user, job_in(shop, 5000), plan=True)
    logs = (
        await session.scalars(
            select(AuditLog).where(AuditLog.entity_id == job.id, AuditLog.entity_type == "job")
        )
    ).all()
    created = next(log for log in logs if log.action == "create")
    assert created.after["recipe"] == shop.recipe.name and "recipe_id" not in created.after
    planned = next(log for log in logs if log.action == "plan")
    assert planned.after["reserved"][shop.wax.code] == "1000"  # не «1E+3» и не «1000.000»
