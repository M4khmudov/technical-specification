"""Наборы данных для расчётов и графиков (Code Interpreter).

Модель не получает базу целиком: только конкретный набор за период, собранный здесь
из того же, что пользователь видит в интерфейсе. Персональные данные заменяются
псевдонимами уже при упаковке в файл (app/assistant/code_interpreter.py).
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import DomainError
from app.models import (
    Batch,
    BatchMovement,
    Material,
    ProductionJob,
    Receipt,
    Recipe,
    RecipeItem,
    Supplier,
)
from app.models.enums import (
    BATCH_STATUS_LABELS,
    JOB_STATUS_LABELS,
    JobStatus,
    MovementKind,
)
from app.services import analytics, production

MAX_ROWS = 20_000
MAX_JOBS = 2000


def _cut(rows: list, limit: int) -> tuple[list, bool]:
    """Не больше limit строк и признак, что выгрузка неполная (молча не обрезаем)."""
    return rows[:limit], len(rows) > limit


MAX_PERIOD_DAYS = 3 * 366


class Dataset(StrEnum):
    CONSUMPTION = "consumption"  # списание материалов в производство
    RECEIPTS = "receipts"  # поступления (партии)
    RECIPE_COSTS = "recipe_costs"  # нормы рецептов и текущая себестоимость материалов
    JOBS = "jobs"  # производственные задания и их себестоимость


@dataclass
class Table:
    name: str
    columns: list[str]
    rows: list[list[object]]
    description: str
    truncated: bool = False  # строк больше предела — выгружена только часть

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(self.columns)
        writer.writerows([_cell(v) for v in row] for row in self.rows)
        return buffer.getvalue()


def _cell(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")  # точка, без экспоненты
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else value


def _week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    date_to = date_to or clock.today()
    date_from = date_from or date_to - timedelta(days=91)  # по умолчанию — квартал
    if date_from > date_to:
        raise DomainError("Начало периода позже конца")
    if (date_to - date_from).days > MAX_PERIOD_DAYS:
        raise DomainError("Период слишком длинный: не больше трёх лет")
    return date_from, date_to


async def _material_ids(session: AsyncSession, material: str | None) -> list[int] | None:
    if not material:
        return None
    found = await analytics.find_materials(session, material, limit=50)
    if not found:
        raise DomainError(f"Материал «{material}» не найден")
    return [m.id for m in found]


async def consumption(
    session: AsyncSession, date_from: date | None, date_to: date | None, material: str | None
) -> Table:
    date_from, date_to = _period(date_from, date_to)
    ids = await _material_ids(session, material)
    day = func.date(BatchMovement.created_at)
    stmt = (
        select(
            day,
            Material.code,
            Material.name,
            Material.unit,
            BatchMovement.quantity,
            Batch.unit_cost_rub,
            ProductionJob.number,
            Recipe.name,
        )
        .join(Batch, Batch.id == BatchMovement.batch_id)
        .join(Material, Material.id == Batch.material_id)
        .join(ProductionJob, ProductionJob.id == BatchMovement.job_id)
        .join(Recipe, Recipe.id == ProductionJob.recipe_id)
        .where(BatchMovement.kind == MovementKind.CONSUME, day.between(date_from, date_to))
        .order_by(day, Material.code)
        .limit(MAX_ROWS + 1)
    )
    if ids is not None:
        stmt = stmt.where(Material.id.in_(ids))
    rows = [
        [d, _week(d), code, name, unit, qty, cost, (qty * cost).quantize(Decimal("0.01")), job, r]
        for d, code, name, unit, qty, cost, job, r in await session.execute(stmt)
    ]
    rows, truncated = _cut(rows, MAX_ROWS)
    return Table(
        f"consumption_{date_from}_{date_to}.csv",
        [
            "date",
            "week_start",
            "material_code",
            "material_name",
            "unit",
            "quantity",
            "unit_cost_rub",
            "cost_rub",
            "job_number",
            "product",
        ],
        rows,
        "Списание материалов в производство по партиям: дата, неделя (понедельник), материал, "
        "количество, себестоимость единицы и сумма в рублях, задание и изделие. Дней и недель "
        "без списаний в выгрузке нет: для графика по периодам строй полный ряд периодов "
        f"с {date_from} по {date_to} и заполняй пропуски нулями.",
        truncated,
    )


async def receipts(
    session: AsyncSession, date_from: date | None, date_to: date | None, material: str | None
) -> Table:
    date_from, date_to = _period(date_from, date_to)
    ids = await _material_ids(session, material)
    stmt = (
        select(Batch, Material, Supplier.name)
        .join(Material, Material.id == Batch.material_id)
        .join(Receipt, Receipt.id == Batch.receipt_id)
        .join(Supplier, Supplier.id == Receipt.supplier_id)
        .where(Batch.received_on.between(date_from, date_to))
        .order_by(Batch.received_on, Batch.number)
        .limit(MAX_ROWS + 1)
    )
    if ids is not None:
        stmt = stmt.where(Material.id.in_(ids))
    rows = [
        [
            b.received_on,
            b.number,
            m.code,
            m.name,
            m.unit,
            b.quantity,
            b.price,
            b.currency.value,
            b.exchange_rate,
            b.unit_cost_rub,
            supplier,
            BATCH_STATUS_LABELS[b.status],
        ]
        for b, m, supplier in await session.execute(stmt)
    ]
    rows, truncated = _cut(rows, MAX_ROWS)
    return Table(
        f"receipts_{date_from}_{date_to}.csv",
        [
            "received_on",
            "batch_number",
            "material_code",
            "material_name",
            "unit",
            "quantity",
            "price",
            "currency",
            "exchange_rate",
            "unit_cost_rub",
            "supplier",
            "status",
        ],
        rows,
        "Поступления: каждая строка — партия с ценой в валюте, курсом и себестоимостью "
        "единицы в рублях.",
        truncated,
    )


async def recipe_costs(session: AsyncSession) -> Table:
    """Нормы рецептов и текущая себестоимость материалов: средняя по свободному остатку
    принятых партий, а если его нет — по последнему поступлению."""
    material_ids = list((await session.scalars(select(RecipeItem.material_id).distinct())).all())
    # Та же цена, что у оценки себестоимости черновика: средняя по остатку принятых
    # партий, иначе последнее поступление — но не брак (раньше брался и он)
    prices = await production.stock_unit_costs(session, material_ids)

    rows = []
    stmt = (
        select(Recipe.name, Material, RecipeItem.qty_per_unit)
        .join(RecipeItem, RecipeItem.recipe_id == Recipe.id)
        .join(Material, Material.id == RecipeItem.material_id)
        .order_by(Recipe.name, Material.code)
    )
    for recipe, m, norm in await session.execute(stmt):
        unit_cost = prices.get(m.id)
        unit_cost = unit_cost.quantize(Decimal("0.000001")) if unit_cost is not None else None
        cost = (norm * unit_cost).quantize(Decimal("0.0001")) if unit_cost is not None else None
        rows.append([recipe, m.code, m.name, m.unit, norm, unit_cost, cost])
    return Table(
        "recipe_costs.csv",
        [
            "product",
            "material_code",
            "material_name",
            "unit",
            "qty_per_candle",
            "unit_cost_rub",
            "cost_per_candle_rub",
        ],
        rows,
        "Нормы материалов на 1 свечу по рецептам и текущая себестоимость единицы материала: "
        "средняя по остатку принятых партий (взвешенно по остатку, включая зарезервированное), "
        "а если остатка нет — по последнему поступлению, кроме брака. Себестоимость свечи = "
        "сумма cost_per_candle_rub по изделию.",
    )


async def jobs(session: AsyncSession, date_from: date | None, date_to: date | None) -> Table:

    date_from, date_to = _period(date_from, date_to)
    ids = (
        await session.scalars(
            select(ProductionJob.id)
            .where(
                ProductionJob.planned_on.between(date_from, date_to),
                ProductionJob.status != JobStatus.CANCELLED,
            )
            .order_by(ProductionJob.planned_on, ProductionJob.number)
            .limit(MAX_JOBS + 1)
        )
    ).all()
    ids, truncated = _cut(list(ids), MAX_JOBS)
    rows = []
    for job_id in ids:
        j = await production.get_job(session, job_id)
        rows.append(
            [
                j.number,
                j.recipe_name,
                j.quantity,
                j.planned_on,
                JOB_STATUS_LABELS[j.status],
                j.cost_rub,
                "факт" if j.cost_is_final else "оценка",
                j.completed_at.date() if j.completed_at else None,
            ]
        )
    return Table(
        f"jobs_{date_from}_{date_to}.csv",
        [
            "job_number",
            "product",
            "quantity",
            "planned_on",
            "status",
            "cost_rub",
            "cost_basis",
            "completed_on",
        ],
        rows,
        "Производственные задания за период со статусом и себестоимостью (факт — по "
        "списанию, оценка — по резерву).",
        truncated,
    )


async def build(
    session: AsyncSession,
    dataset: Dataset,
    date_from: date | None = None,
    date_to: date | None = None,
    material: str | None = None,
) -> Table:
    if dataset == Dataset.CONSUMPTION:
        return await consumption(session, date_from, date_to, material)
    if dataset == Dataset.RECEIPTS:
        return await receipts(session, date_from, date_to, material)
    if dataset == Dataset.RECIPE_COSTS:
        return await recipe_costs(session)
    return await jobs(session, date_from, date_to)
