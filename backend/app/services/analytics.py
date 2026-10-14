"""Аналитические запросы для ассистента и интерфейса: остатки, дефицит за период,
разбивка себестоимости задания. Только чтение."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, NotFoundError
from app.models import Batch, Material, ProductionJob, Recipe
from app.models.enums import BatchStatus, JobStatus
from app.schemas.production import JobOut
from app.services import production

ZERO = Decimal("0")
ACTIVE_JOBS = (JobStatus.DRAFT, JobStatus.PLANNED, JobStatus.IN_PROGRESS)


# ---------------------------------------------------------------- поиск по тексту


def _stems(text: str) -> list[str]:
    """Грубая основа слов для поиска без учёта окончаний:
    «соевого воска» → «соев», «вос»; «кокосового» → «кокосов»."""
    words = [w for w in text.lower().replace("«", " ").replace("»", " ").split() if len(w) >= 3]
    return [w[: max(3, len(w) - 3)] for w in words]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def find_materials(session: AsyncSession, query: str, limit: int = 10) -> list[Material]:
    """Материал по коду (точно) или по названию (все слова, без учёта окончаний и порядка)."""
    query = query.strip()
    if not query:
        return []
    exact = await session.scalar(select(Material).where(func.lower(Material.code) == query.lower()))
    if exact:
        return [exact]
    stems = _stems(query) or [query.lower()]
    conditions = [
        or_(Material.name.ilike(f"%{_escape(s)}%"), Material.code.ilike(f"%{_escape(s)}%"))
        for s in stems
    ]
    rows = await session.scalars(select(Material).where(and_(*conditions)).order_by(Material.code))
    return list(rows.all())[:limit]


async def find_recipe(session: AsyncSession, name: str) -> Recipe:
    recipes = list((await session.scalars(select(Recipe).order_by(Recipe.name))).all())
    wanted = name.strip().lower().strip("«»\"' ")
    exact = [r for r in recipes if r.name.lower() == wanted]
    if exact:
        return exact[0]
    stems = _stems(wanted) or [wanted]
    matches = [r for r in recipes if all(s in r.name.lower() for s in stems)]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(f"«{r.name}»" for r in recipes)
    if not matches:
        raise NotFoundError(f"Рецепт «{name}» не найден. Есть рецепты: {names}")
    raise DomainError(f"Название «{name}» подходит к нескольким рецептам: {names}")


async def job_by_number(session: AsyncSession, number: int) -> JobOut:
    job_id = await session.scalar(select(ProductionJob.id).where(ProductionJob.number == number))
    if job_id is None:
        raise NotFoundError(f"Задание №{number} не найдено")
    return await production.get_job(session, job_id)


async def batch_id_by_number(session: AsyncSession, number: int) -> int:
    batch_id = await session.scalar(select(Batch.id).where(Batch.number == number))
    if batch_id is None:
        raise NotFoundError(f"Партия №{number} не найдена")
    return batch_id


# ---------------------------------------------------------------- остатки


@dataclass
class StockRow:
    material: Material
    on_hand: Decimal  # физический остаток в принятых партиях
    reserved: Decimal
    free: Decimal  # можно зарезервировать
    pending: Decimal  # в партиях «На проверке» — пока использовать нельзя
    batches: int  # принятых партий с остатком


async def material_stock(session: AsyncSession, materials: list[Material]) -> list[StockRow]:
    if not materials:
        return []
    ids = [m.id for m in materials]
    accepted = Batch.status == BatchStatus.ACCEPTED
    rows = await session.execute(
        select(
            Batch.material_id,
            func.coalesce(func.sum(Batch.remaining_qty).filter(accepted), 0),
            func.coalesce(func.sum(Batch.reserved_qty).filter(accepted), 0),
            func.coalesce(
                func.sum(Batch.remaining_qty).filter(Batch.status == BatchStatus.PENDING), 0
            ),
            func.count().filter(and_(accepted, Batch.remaining_qty > 0)),
        )
        .where(Batch.material_id.in_(ids))
        .group_by(Batch.material_id)
    )
    totals = {mid: (Decimal(h), Decimal(r), Decimal(p), n) for mid, h, r, p, n in rows}
    result = []
    for m in materials:
        on_hand, reserved, pending, count = totals.get(m.id, (ZERO, ZERO, ZERO, 0))
        result.append(StockRow(m, on_hand, reserved, on_hand - reserved, pending, count))
    return result


@dataclass
class PriceRow:
    material: Material
    # Средняя себестоимость единицы свободного и зарезервированного остатка принятых
    # партий (взвешенная по остатку); None — принятого остатка нет
    average_cost_rub: Decimal | None
    stock_qty: Decimal
    last_receipts: list[Batch]  # последние поступления (без забракованных), новые первыми


async def material_prices(
    session: AsyncSession, materials: list[Material], last: int = 3
) -> list[PriceRow]:
    """Сколько стоит материал: средняя себестоимость остатка и цены последних поступлений.
    Забракованные партии не учитываются — по ним материал не закупался в учёт."""
    result = []
    for m in materials:
        accepted = and_(
            Batch.material_id == m.id,
            Batch.status == BatchStatus.ACCEPTED,
            Batch.remaining_qty > 0,
        )
        qty, amount = (
            await session.execute(
                select(
                    func.coalesce(func.sum(Batch.remaining_qty), 0),
                    func.coalesce(func.sum(Batch.remaining_qty * Batch.unit_cost_rub), 0),
                ).where(accepted)
            )
        ).one()
        qty, amount = Decimal(qty), Decimal(amount)
        average = (amount / qty).quantize(Decimal("0.01")) if qty > 0 else None
        receipts = await session.scalars(
            select(Batch)
            .where(Batch.material_id == m.id, Batch.status != BatchStatus.REJECTED)
            .order_by(Batch.received_on.desc(), Batch.id.desc())
            .limit(last)
        )
        result.append(PriceRow(m, average, qty, list(receipts.all())))
    return result


async def free_stock(session: AsyncSession, material_ids: list[int]) -> dict[int, Decimal]:
    rows = await session.execute(
        select(Batch.material_id, func.sum(Batch.remaining_qty - Batch.reserved_qty))
        .where(Batch.material_id.in_(material_ids), Batch.status == BatchStatus.ACCEPTED)
        .group_by(Batch.material_id)
    )
    return {mid: Decimal(q) for mid, q in rows}


# ---------------------------------------------------------------- дефицит за период


@dataclass
class MaterialShortage:
    material_id: int
    code: str
    name: str
    unit: str
    planned_deficit: Decimal = ZERO  # не хватило при резервировании запланированных заданий
    draft_need: Decimal = ZERO  # нужно черновикам (они ещё ничего не резервировали)
    free_now: Decimal = ZERO  # свободный остаток сейчас
    jobs: list[int] = field(default_factory=list)

    @property
    def shortage(self) -> Decimal:
        """Сколько не хватит, даже если весь свободный остаток пустить на эти задания."""
        return max(self.planned_deficit + self.draft_need - self.free_now, ZERO)


@dataclass
class DeficitReport:
    date_from: date
    date_to: date
    jobs: list[JobOut]
    materials: list[MaterialShortage]


MAX_PERIOD_DAYS = 366


async def deficit_for_period(
    session: AsyncSession, date_from: date, date_to: date
) -> DeficitReport:
    if date_from > date_to:
        raise DomainError("Начало периода позже конца")
    if (date_to - date_from).days > MAX_PERIOD_DAYS:
        raise DomainError("Период слишком длинный: не больше года")
    job_ids = (
        await session.scalars(
            select(ProductionJob.id)
            .where(
                ProductionJob.planned_on.between(date_from, date_to),
                ProductionJob.status.in_(ACTIVE_JOBS),
            )
            .order_by(ProductionJob.planned_on, ProductionJob.number)
        )
    ).all()
    jobs = [await production.get_job(session, jid) for jid in job_ids]

    shortages: dict[int, MaterialShortage] = {}
    for job in jobs:
        for m in job.materials:
            row = shortages.setdefault(
                m.material_id,
                MaterialShortage(m.material_id, m.material_code, m.material_name, m.unit),
            )
            if job.status == JobStatus.DRAFT:
                row.draft_need += m.required_qty
            else:
                row.planned_deficit += m.deficit_qty
            if job.status == JobStatus.DRAFT or m.deficit_qty > 0:
                row.jobs.append(job.number)
    free = await free_stock(session, list(shortages))
    for mid, row in shortages.items():
        row.free_now = free.get(mid, ZERO)
    materials = sorted(
        (r for r in shortages.values() if r.shortage > 0), key=lambda r: (-r.shortage, r.code)
    )
    return DeficitReport(date_from, date_to, jobs, materials)


# ---------------------------------------------------------------- себестоимость задания


@dataclass
class CostLine:
    material_code: str
    material_name: str
    unit: str
    batch_number: int | None  # None — оценка черновика по средней цене склада
    quantity: Decimal
    unit_cost_rub: Decimal

    @property
    def amount(self) -> Decimal:
        return production.money(self.quantity * self.unit_cost_rub)


@dataclass
class CostReport:
    job: JobOut
    lines: list[CostLine]
    uncovered: list[tuple[str, str, Decimal, str]]  # код, название, дефицит, единица


async def job_cost_breakdown(session: AsyncSession, number: int) -> CostReport:
    job = await job_by_number(session, number)
    materials = {m.material_id: m for m in job.materials}
    lines: list[CostLine] = []
    if job.cost_is_final:
        for mid, bn, cost, qty in await production.consumed_lines(session, job.id):
            m = materials[mid]
            lines.append(CostLine(m.material_code, m.material_name, m.unit, bn, qty, cost))
    elif job.cost_basis == "stock_average":
        prices = await production.stock_unit_costs(session, list(materials))
        for mid, m in materials.items():
            if mid in prices:
                lines.append(
                    CostLine(
                        m.material_code, m.material_name, m.unit, None, m.required_qty, prices[mid]
                    )
                )
    else:
        for r in job.reservations:
            m = materials[r.material_id]
            lines.append(
                CostLine(
                    m.material_code,
                    m.material_name,
                    m.unit,
                    r.batch_number,
                    r.quantity,
                    r.unit_cost_rub,
                )
            )
    uncovered = [
        (m.material_code, m.material_name, m.deficit_qty, m.unit)
        for m in job.materials
        if m.deficit_qty > 0
    ]
    return CostReport(job, lines, uncovered)
