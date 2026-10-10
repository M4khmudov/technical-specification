"""Производственные задания и резервы материалов.

Жизненный цикл: Черновик → Запланировано → В работе → Завершено → Сервис маркировки.
Отмена — только из «Черновика» и «Запланировано».

Конкурентность. Каждая операция сначала берёт блокировку строки задания, затем
блокирует нужные партии через SELECT … FOR UPDATE в едином порядке
(материал → дата поступления → id). В READ COMMITTED PostgreSQL после ожидания
блокировки перечитывает строку и заново проверяет условие «есть свободный остаток»,
поэтому два одновременных резерва не могут взять один и тот же остаток, а единый
порядок блокировок исключает взаимоблокировки. CHECK reserved_qty <= remaining_qty
в БД — последний рубеж, если в коде появится ошибка.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.core.format import fmt_qty
from app.core.permissions import Permission, require
from app.models import (
    Batch,
    BatchMovement,
    JobMaterial,
    ProductionJob,
    Recipe,
    Reservation,
    User,
)
from app.models.enums import JOB_STATUS_LABELS, BatchStatus, JobStatus, MovementKind
from app.schemas.production import (
    JobCompleteIn,
    JobCreateIn,
    JobListItem,
    JobMaterialOut,
    JobOut,
    Light,
    ReservationOut,
)
from app.schemas.stock import Page
from app.services import audit, marking

ZERO = Decimal("0")
CANCELLABLE = (JobStatus.DRAFT, JobStatus.PLANNED)
FINISHED = (JobStatus.COMPLETED, JobStatus.MARKED, JobStatus.CANCELLED)


def _now() -> datetime:
    return datetime.now(UTC)


def _label(status: JobStatus) -> str:
    return JOB_STATUS_LABELS[status]


# ---------------------------------------------------------------- загрузка и блокировки


def _job_query():
    return select(ProductionJob).options(
        selectinload(ProductionJob.recipe),
        selectinload(ProductionJob.materials).selectinload(JobMaterial.material),
        selectinload(ProductionJob.reservations).selectinload(Reservation.batch),
    )


async def _lock_job(session: AsyncSession, job_id: int) -> ProductionJob:
    job = await session.scalar(
        _job_query()
        .where(ProductionJob.id == job_id)
        .with_for_update(of=ProductionJob)
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise NotFoundError(f"Задание #{job_id} не найдено")
    return job


async def _lock_batches(
    session: AsyncSession, material_ids: set[int], also_batch_ids: set[int] = frozenset()
) -> list[Batch]:
    """Блокирует принятые партии материалов со свободным остатком (+ явно указанные).

    Порядок строк = порядок блокировок = FIFO: материал, дата поступления, id.
    populate_existing — чтобы взять свежие значения после ожидания блокировки,
    а не устаревшие объекты из identity map сессии.
    """
    if not material_ids and not also_batch_ids:
        return []
    available = and_(Batch.status == BatchStatus.ACCEPTED, Batch.remaining_qty > Batch.reserved_qty)
    condition = or_(available, Batch.id.in_(also_batch_ids)) if also_batch_ids else available
    stmt = (
        select(Batch)
        .where(Batch.material_id.in_(material_ids), condition)
        .order_by(Batch.material_id, Batch.received_on, Batch.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list((await session.scalars(stmt)).all())


def _reserved_by_material(job: ProductionJob) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for r in job.reservations:
        totals[r.material_id] += r.quantity
    return totals


def _deficits(job: ProductionJob) -> dict[int, Decimal]:
    reserved = _reserved_by_material(job)
    return {
        jm.material_id: jm.required_qty - reserved[jm.material_id]
        for jm in job.materials
        if jm.required_qty > reserved[jm.material_id]
    }


def _describe_shortage(job: ProductionJob, shortage: dict[int, Decimal]) -> str:
    materials = {jm.material_id: jm.material for jm in job.materials}
    return ", ".join(
        f"{fmt_qty(qty, materials[mid].unit)} «{materials[mid].name}»"
        for mid, qty in sorted(shortage.items())
    )


def _reservation_totals(job: ProductionJob) -> dict[str, str]:
    return {str(mid): str(qty) for mid, qty in sorted(_reserved_by_material(job).items())}


async def _reserve_deficits(session: AsyncSession, actor: User, job: ProductionJob) -> None:
    """Резервирует недостающее по FIFO. При нехватке резервирует сколько есть."""
    need = _deficits(job)
    if not need:
        return
    existing = {r.batch_id: r for r in job.reservations}
    for batch in await _lock_batches(session, set(need)):
        left = need.get(batch.material_id, ZERO)
        take = min(batch.free_qty, left)
        if take <= 0:
            continue
        batch.reserved_qty += take
        if batch.id in existing:
            existing[batch.id].quantity += take
        else:
            reservation = Reservation(
                batch_id=batch.id, material_id=batch.material_id, quantity=take, batch=batch
            )
            job.reservations.append(reservation)
            existing[batch.id] = reservation
        session.add(
            BatchMovement(
                batch_id=batch.id,
                job_id=job.id,
                kind=MovementKind.RESERVE,
                quantity=take,
                user_id=actor.id,
                note=f"Резерв под задание №{job.number}",
            )
        )
        need[batch.material_id] = left - take


def _require_status(job: ProductionJob, allowed: tuple[JobStatus, ...], action: str) -> None:
    if job.status not in allowed:
        allowed_text = " или ".join(f"«{_label(s)}»" for s in allowed)
        raise ConflictError(
            f"Задание №{job.number} в статусе «{_label(job.status)}»: "
            f"{action} можно только задание в статусе {allowed_text}"
        )


# ---------------------------------------------------------------- операции


async def create_job(
    session: AsyncSession, actor: User, data: JobCreateIn, *, plan: bool = False
) -> JobOut:
    """Создать задание (Черновик); с plan=True — сразу запланировать и зарезервировать."""
    require(actor, Permission.JOB_MANAGE)
    recipe = await session.scalar(
        select(Recipe).options(selectinload(Recipe.items)).where(Recipe.id == data.recipe_id)
    )
    if recipe is None:
        raise NotFoundError(f"Рецепт #{data.recipe_id} не найден")
    if not recipe.items:
        raise DomainError(f"В рецепте «{recipe.name}» нет материалов")
    if data.planned_on < date.today():
        raise DomainError("Дата задания не может быть в прошлом")

    job = ProductionJob(
        recipe_id=recipe.id,
        quantity=data.quantity,
        planned_on=data.planned_on,
        comment=data.comment,
        created_by_id=actor.id,
        status=JobStatus.DRAFT,
        materials=[
            JobMaterial(
                material_id=item.material_id, required_qty=item.qty_per_unit * data.quantity
            )
            for item in recipe.items
        ],
        reservations=[],
    )
    session.add(job)
    await session.flush()
    audit.record(
        session,
        actor,
        "job",
        job.id,
        "create",
        after=audit.snapshot(job, ["number", "recipe_id", "quantity", "planned_on", "status"]),
    )
    await session.commit()
    if plan:
        return await plan_job(session, actor, job.id)
    return await get_job(session, job.id)


async def plan_job(session: AsyncSession, actor: User, job_id: int) -> JobOut:
    require(actor, Permission.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _require_status(job, (JobStatus.DRAFT,), "запланировать")
    job.status = JobStatus.PLANNED
    await _reserve_deficits(session, actor, job)
    audit.record(
        session,
        actor,
        "job",
        job.id,
        "plan",
        before={"status": JobStatus.DRAFT.value},
        after={"status": job.status.value, "reserved": _reservation_totals(job)},
    )
    await session.commit()
    return await get_job(session, job_id)


async def reserve_job(session: AsyncSession, actor: User, job_id: int) -> JobOut:
    """Дозарезервировать дефицит (например, после приёмки новой партии)."""
    require(actor, Permission.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _require_status(job, (JobStatus.PLANNED,), "дозарезервировать")
    before = _reservation_totals(job)
    await _reserve_deficits(session, actor, job)
    after = _reservation_totals(job)
    if after != before:
        audit.record(
            session,
            actor,
            "job",
            job.id,
            "reserve",
            before={"reserved": before},
            after={"reserved": after},
        )
    await session.commit()
    return await get_job(session, job_id)


async def start_job(session: AsyncSession, actor: User, job_id: int) -> JobOut:
    require(actor, Permission.JOB_RUN)
    job = await _lock_job(session, job_id)
    _require_status(job, (JobStatus.PLANNED,), "запустить")
    deficits = _deficits(job)
    if deficits:
        raise DomainError(
            f"Нельзя запустить задание №{job.number}: не хватает "
            f"{_describe_shortage(job, deficits)}"
        )
    job.status = JobStatus.IN_PROGRESS
    job.started_at = _now()
    audit.record(
        session,
        actor,
        "job",
        job.id,
        "start",
        before={"status": JobStatus.PLANNED.value},
        after={"status": job.status.value},
    )
    await session.commit()
    return await get_job(session, job_id)


@dataclass
class _Plan:
    """План списания при завершении: сначала считаем, потом применяем."""

    consume: list[tuple[Batch, Decimal, str]] = field(default_factory=list)
    release: list[tuple[Batch, Decimal]] = field(default_factory=list)
    shortage: dict[int, Decimal] = field(default_factory=dict)


async def complete_job(
    session: AsyncSession, actor: User, job_id: int, data: JobCompleteIn
) -> JobOut:
    """Завершить задание с фактическим расходом.

    Резерв списывается по FIFO; экономия (факт < резерва) возвращается в партии,
    перерасход (факт > резерва) досписывается из свободного остатка по FIFO.
    """
    require(actor, Permission.JOB_RUN)
    job = await _lock_job(session, job_id)
    _require_status(job, (JobStatus.IN_PROGRESS,), "завершить")

    by_material = {jm.material_id: jm for jm in job.materials}
    actual: dict[int, Decimal] = {}
    for item in data.actuals:
        if item.material_id not in by_material:
            raise DomainError(f"Материал #{item.material_id} не входит в задание №{job.number}")
        if item.material_id in actual:
            raise DomainError(f"Материал #{item.material_id} указан дважды")
        actual[item.material_id] = item.actual_qty
    for mid, jm in by_material.items():
        actual.setdefault(mid, jm.required_qty)

    batches = await _lock_batches(
        session, set(by_material), also_batch_ids={r.batch_id for r in job.reservations}
    )
    reserved_in = {r.batch_id: r.quantity for r in job.reservations}
    plan = _Plan()
    for mid in sorted(by_material):
        left = actual[mid]
        own = [b for b in batches if b.material_id == mid]
        # 1) списываем из своего резерва по FIFO, остаток резерва — экономия
        for batch in own:
            reserved = reserved_in.get(batch.id, ZERO)
            if reserved <= 0:
                continue
            take = min(reserved, left)
            if take > 0:
                plan.consume.append((batch, take, f"Списание по заданию №{job.number}"))
            if reserved - take > 0:
                plan.release.append((batch, reserved - take))
            left -= take
        # 2) перерасход — из свободного остатка по FIFO
        for batch in own:
            if left <= 0:
                break
            if batch.status != BatchStatus.ACCEPTED:
                continue
            # свободное после возврата экономии по этой же партии
            released = sum((q for b, q in plan.release if b is batch), ZERO)
            take = min(batch.free_qty + released, left)
            if take > 0:
                plan.consume.append((batch, take, f"Перерасход по заданию №{job.number}"))
                left -= take
        if left > 0:
            plan.shortage[mid] = left

    if plan.shortage:
        raise DomainError(
            f"Нельзя завершить задание №{job.number}: перерасход не покрыт свободным "
            f"остатком, не хватает {_describe_shortage(job, plan.shortage)}"
        )

    for batch, qty in plan.release:
        reserved_in[batch.id] -= qty
        batch.reserved_qty -= qty
        _movement(session, actor, job, batch, MovementKind.RELEASE, qty, "Экономия по заданию")
    for batch, qty, note in plan.consume:
        from_reserve = min(qty, reserved_in.get(batch.id, ZERO))
        reserved_in[batch.id] = reserved_in.get(batch.id, ZERO) - from_reserve
        batch.reserved_qty -= from_reserve
        batch.remaining_qty -= qty
        _movement(session, actor, job, batch, MovementKind.CONSUME, qty, note)

    for mid, jm in by_material.items():
        jm.actual_qty = actual[mid]
    job.reservations.clear()
    job.status = JobStatus.COMPLETED
    job.completed_at = _now()
    audit.record(
        session,
        actor,
        "job",
        job.id,
        "complete",
        before={"status": JobStatus.IN_PROGRESS.value},
        after={
            "status": job.status.value,
            "actual": {str(mid): str(q) for mid, q in sorted(actual.items())},
        },
    )
    # Отчёт в сервис маркировки — в той же транзакции (outbox): завершение не ждёт
    # сервис, а отчёт не потеряется, даже если очередь сейчас недоступна
    report = marking.build_report(job)
    session.add(report)
    await session.commit()
    await marking.enqueue(report.id)
    return await get_job(session, job_id)


def _movement(
    session: AsyncSession,
    actor: User,
    job: ProductionJob,
    batch: Batch,
    kind: MovementKind,
    qty: Decimal,
    note: str,
) -> None:
    session.add(
        BatchMovement(
            batch_id=batch.id,
            job_id=job.id,
            kind=kind,
            quantity=qty,
            user_id=actor.id,
            note=f"{note} №{job.number}" if kind == MovementKind.RELEASE else note,
        )
    )


async def cancel_job(session: AsyncSession, actor: User, job_id: int) -> JobOut:
    require(actor, Permission.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _require_status(job, CANCELLABLE, "отменить")
    before_status = job.status
    released = _reservation_totals(job)
    batches = {
        b.id: b
        for b in await _lock_batches(
            session,
            {r.material_id for r in job.reservations},
            also_batch_ids={r.batch_id for r in job.reservations},
        )
    }
    for r in job.reservations:
        batch = batches[r.batch_id]
        batch.reserved_qty -= r.quantity
        _movement(session, actor, job, batch, MovementKind.RELEASE, r.quantity, "Отмена задания")
    job.reservations.clear()
    job.status = JobStatus.CANCELLED
    job.cancelled_at = _now()
    audit.record(
        session,
        actor,
        "job",
        job.id,
        "cancel",
        before={"status": before_status.value, "reserved": released},
        after={"status": job.status.value},
    )
    await session.commit()
    return await get_job(session, job_id)


# ---------------------------------------------------------------- чтение


def _light(job: ProductionJob, required: Decimal, reserved: Decimal) -> Light | None:
    if job.status in FINISHED:
        return None
    if reserved >= required:
        return Light.GREEN
    return Light.YELLOW if reserved > 0 else Light.RED


async def get_job(session: AsyncSession, job_id: int) -> JobOut:
    job = await session.scalar(
        _job_query().where(ProductionJob.id == job_id).execution_options(populate_existing=True)
    )
    if job is None:
        raise NotFoundError(f"Задание #{job_id} не найдено")

    reserved = _reserved_by_material(job)
    materials = [
        JobMaterialOut(
            material_id=jm.material_id,
            material_code=jm.material.code,
            material_name=jm.material.name,
            unit=jm.material.unit,
            required_qty=jm.required_qty,
            reserved_qty=reserved[jm.material_id],
            deficit_qty=max(jm.required_qty - reserved[jm.material_id], ZERO)
            if job.status not in FINISHED
            else ZERO,
            actual_qty=jm.actual_qty,
            light=_light(job, jm.required_qty, reserved[jm.material_id]),
        )
        for jm in job.materials
    ]
    reservations = sorted(
        (
            ReservationOut(
                batch_id=r.batch_id,
                batch_number=r.batch.number,
                material_id=r.material_id,
                received_on=r.batch.received_on,
                quantity=r.quantity,
                unit_cost_rub=r.batch.unit_cost_rub,
            )
            for r in job.reservations
        ),
        key=lambda r: (r.material_id, r.received_on, r.batch_id),
    )

    final = job.status in (JobStatus.COMPLETED, JobStatus.MARKED)
    if final:
        cost = await session.scalar(
            select(func.coalesce(func.sum(BatchMovement.quantity * Batch.unit_cost_rub), 0))
            .join(Batch, Batch.id == BatchMovement.batch_id)
            .where(BatchMovement.job_id == job.id, BatchMovement.kind == MovementKind.CONSUME)
        )
    else:
        cost = sum((r.quantity * r.unit_cost_rub for r in reservations), ZERO)
    has_deficit = any(m.deficit_qty > 0 for m in materials)

    return JobOut(
        id=job.id,
        number=job.number,
        recipe_id=job.recipe_id,
        recipe_name=job.recipe.name,
        quantity=job.quantity,
        planned_on=job.planned_on,
        status=job.status,
        status_label=_label(job.status),
        comment=job.comment,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        cancelled_at=job.cancelled_at,
        has_deficit=has_deficit,
        materials=materials,
        reservations=reservations,
        cost_rub=Decimal(cost).quantize(Decimal("0.01")),
        cost_is_final=final,
        cost_covers_all=final or not has_deficit,
    )


async def list_jobs(
    session: AsyncSession,
    status: JobStatus | None = None,
    planned_from: date | None = None,
    planned_to: date | None = None,
    page: int = 1,
    size: int = 20,
) -> Page[JobListItem]:
    reserved_sum = (
        select(func.coalesce(func.sum(Reservation.quantity), 0))
        .where(
            Reservation.job_id == JobMaterial.job_id,
            Reservation.material_id == JobMaterial.material_id,
        )
        .scalar_subquery()
    )
    deficit = exists().where(
        JobMaterial.job_id == ProductionJob.id, JobMaterial.required_qty > reserved_sum
    )
    stmt = select(ProductionJob, Recipe.name, deficit.label("has_deficit")).join(
        Recipe, Recipe.id == ProductionJob.recipe_id
    )
    if status is not None:
        stmt = stmt.where(ProductionJob.status == status)
    if planned_from is not None:
        stmt = stmt.where(ProductionJob.planned_on >= planned_from)
    if planned_to is not None:
        stmt = stmt.where(ProductionJob.planned_on <= planned_to)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.execute(
            stmt.order_by(ProductionJob.planned_on.desc(), ProductionJob.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return Page[JobListItem](
        items=[
            JobListItem(
                id=job.id,
                number=job.number,
                recipe_name=name,
                quantity=job.quantity,
                planned_on=job.planned_on,
                status=job.status,
                status_label=_label(job.status),
                has_deficit=bool(has_def) and job.status not in FINISHED,
            )
            for job, name, has_def in rows
        ],
        total=total or 0,
        page=page,
        size=size,
    )
