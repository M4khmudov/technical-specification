"""Поступления и партии: приход, приёмка/брак, реестр, история партии."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.core.permissions import Permission, require
from app.models import (
    AuditLog,
    Batch,
    BatchMovement,
    Material,
    ProductionJob,
    Receipt,
    Supplier,
    User,
)
from app.models.enums import BATCH_STATUS_LABELS, BatchStatus, Currency, MovementKind
from app.schemas.stock import (
    AuditOut,
    BatchDetailOut,
    BatchFilter,
    BatchOut,
    BatchSort,
    MovementOut,
    Page,
    ReceiptIn,
    ReceiptOut,
)
from app.services import audit
from app.services.rates import RateQuote, RatesProvider

UNIT_COST_STEP = Decimal("0.000001")  # точность numeric(16, 6)
AUDIT_FIELDS = ["number", "material_id", "quantity", "price", "currency", "status", "comment"]


def batch_to_out(b: Batch) -> BatchOut:
    return BatchOut(
        id=b.id,
        number=b.number,
        received_on=b.received_on,
        material_id=b.material_id,
        material_code=b.material.code,
        material_name=b.material.name,
        unit=b.material.unit,
        supplier_id=b.receipt.supplier_id,
        supplier_name=b.receipt.supplier.name,
        quantity=b.quantity,
        remaining_qty=b.remaining_qty,
        reserved_qty=b.reserved_qty,
        free_qty=b.free_qty,
        price=b.price,
        currency=b.currency,
        exchange_rate=b.exchange_rate,
        rate_date=b.rate_date,
        unit_cost_rub=b.unit_cost_rub,
        status=b.status,
        comment=b.comment,
    )


def _with_refs(stmt: Select) -> Select:
    return stmt.options(
        joinedload(Batch.material), joinedload(Batch.receipt).joinedload(Receipt.supplier)
    )


async def create_receipt(
    session: AsyncSession, actor: User, data: ReceiptIn, rates: RatesProvider
) -> ReceiptOut:
    """Оформить поступление: каждая строка становится партией «На проверке»."""
    require(actor, Permission.RECEIPT_CREATE)
    if data.received_on > date.today():
        raise DomainError("Дата поступления не может быть в будущем")

    supplier = await session.get(Supplier, data.supplier_id)
    if supplier is None:
        raise NotFoundError(f"Поставщик #{data.supplier_id} не найден")

    material_ids = {line.material_id for line in data.lines}
    materials = (await session.scalars(select(Material).where(Material.id.in_(material_ids)))).all()
    missing = material_ids - {m.id for m in materials}
    if missing:
        raise NotFoundError(f"Материал #{min(missing)} не найден")

    # Курсы получаем до любых записей в БД: если сервис курсов недоступен,
    # поступление не создаётся частично.
    quotes: dict[Currency, RateQuote] = {}
    for currency in {line.currency for line in data.lines}:
        quotes[currency] = await rates.get_rate(currency, data.received_on)

    receipt = Receipt(supplier_id=supplier.id, received_on=data.received_on, created_by_id=actor.id)
    session.add(receipt)
    await session.flush()

    batches: list[Batch] = []
    for line in data.lines:
        quote = quotes[line.currency]
        batch = Batch(
            receipt_id=receipt.id,
            material_id=line.material_id,
            received_on=data.received_on,
            quantity=line.quantity,
            remaining_qty=line.quantity,
            reserved_qty=Decimal("0"),
            price=line.price,
            currency=line.currency,
            exchange_rate=quote.rate,
            rate_date=quote.rate_date,
            unit_cost_rub=(line.price * quote.rate).quantize(UNIT_COST_STEP, ROUND_HALF_UP),
            status=BatchStatus.PENDING,
            comment=line.comment,
        )
        session.add(batch)
        batches.append(batch)
    await session.flush()

    for batch in batches:
        session.add(
            BatchMovement(
                batch_id=batch.id,
                kind=MovementKind.RECEIPT,
                quantity=batch.quantity,
                user_id=actor.id,
                note=f"Поступление #{receipt.id}",
            )
        )
        audit.record(
            session, actor, "batch", batch.id, "create", after=audit.snapshot(batch, AUDIT_FIELDS)
        )
    await session.commit()

    loaded = await session.scalars(
        _with_refs(select(Batch).where(Batch.receipt_id == receipt.id).order_by(Batch.id))
    )
    return ReceiptOut(
        id=receipt.id,
        supplier_id=receipt.supplier_id,
        received_on=receipt.received_on,
        batches=[batch_to_out(b) for b in loaded],
    )


async def _set_status(
    session: AsyncSession, actor: User, batch_id: int, new_status: BatchStatus, reason: str | None
) -> BatchOut:
    require(actor, Permission.BATCH_REVIEW)
    # FOR UPDATE: одновременные «принять» и «брак» по одной партии выполнятся по очереди
    batch = await session.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFoundError(f"Партия #{batch_id} не найдена")
    if batch.status != BatchStatus.PENDING:
        raise ConflictError(
            f"Партия №{batch.number} уже в статусе «{BATCH_STATUS_LABELS[batch.status]}», "
            "менять статус можно только у партии «На проверке»"
        )
    before = audit.snapshot(batch, ["status"])
    batch.status = new_status
    after = audit.snapshot(batch, ["status"])
    if reason:
        after["reason"] = reason
    action = "accept" if new_status == BatchStatus.ACCEPTED else "reject"
    audit.record(session, actor, "batch", batch.id, action, before=before, after=after)
    await session.commit()
    return await get_batch(session, batch_id)


async def accept_batch(session: AsyncSession, actor: User, batch_id: int) -> BatchOut:
    return await _set_status(session, actor, batch_id, BatchStatus.ACCEPTED, None)


async def reject_batch(
    session: AsyncSession, actor: User, batch_id: int, reason: str | None = None
) -> BatchOut:
    return await _set_status(session, actor, batch_id, BatchStatus.REJECTED, reason)


async def get_batch(session: AsyncSession, batch_id: int) -> BatchOut:
    batch = await session.scalar(
        _with_refs(select(Batch).where(Batch.id == batch_id)).execution_options(
            populate_existing=True
        )
    )
    if batch is None:
        raise NotFoundError(f"Партия #{batch_id} не найдена")
    return batch_to_out(batch)


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_SORT_COLUMNS = {
    BatchSort.NUMBER: Batch.number,
    BatchSort.RECEIVED_ON: Batch.received_on,
    BatchSort.MATERIAL: Material.name,
    BatchSort.SUPPLIER: Supplier.name,
    BatchSort.QUANTITY: Batch.quantity,
    BatchSort.FREE_QTY: Batch.remaining_qty - Batch.reserved_qty,
    BatchSort.UNIT_COST_RUB: Batch.unit_cost_rub,
    BatchSort.STATUS: Batch.status,
    BatchSort.CURRENCY: Batch.currency,
}


async def list_batches(
    session: AsyncSession,
    filters: BatchFilter,
    sort: BatchSort = BatchSort.NUMBER,
    descending: bool = True,
    page: int = 1,
    size: int = 20,
) -> Page[BatchOut]:
    """Реестр партий: фильтры по колонкам, сортировка и пагинация на стороне БД."""
    stmt = (
        select(Batch)
        .join(Material, Material.id == Batch.material_id)
        .join(Receipt, Receipt.id == Batch.receipt_id)
        .join(Supplier, Supplier.id == Receipt.supplier_id)
    )
    if filters.number is not None:
        stmt = stmt.where(Batch.number == filters.number)
    if filters.material_id is not None:
        stmt = stmt.where(Batch.material_id == filters.material_id)
    if filters.material:
        pattern = _like(filters.material)
        stmt = stmt.where(or_(Material.code.ilike(pattern), Material.name.ilike(pattern)))
    if filters.supplier:
        stmt = stmt.where(Supplier.name.ilike(_like(filters.supplier)))
    if filters.status is not None:
        stmt = stmt.where(Batch.status == filters.status)
    if filters.currency is not None:
        stmt = stmt.where(Batch.currency == filters.currency)
    if filters.received_from is not None:
        stmt = stmt.where(Batch.received_on >= filters.received_from)
    if filters.received_to is not None:
        stmt = stmt.where(Batch.received_on <= filters.received_to)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))

    column = _SORT_COLUMNS[sort]
    order = column.desc() if descending else column.asc()
    # id — второй ключ, чтобы порядок был стабильным между страницами
    stmt = stmt.order_by(order, Batch.id.desc() if descending else Batch.id.asc())
    stmt = _with_refs(stmt).offset((page - 1) * size).limit(size)
    items = (await session.scalars(stmt)).all()
    return Page[BatchOut](
        items=[batch_to_out(b) for b in items], total=total or 0, page=page, size=size
    )


async def get_batch_history(session: AsyncSession, batch_id: int) -> BatchDetailOut:
    """Карточка партии: данные, движения (резервы/списания по заданиям) и журнал изменений."""
    batch = await get_batch(session, batch_id)
    movements = (
        await session.execute(
            select(BatchMovement, ProductionJob.number, User.full_name)
            .outerjoin(ProductionJob, ProductionJob.id == BatchMovement.job_id)
            .outerjoin(User, User.id == BatchMovement.user_id)
            .where(BatchMovement.batch_id == batch_id)
            .order_by(BatchMovement.id)
        )
    ).all()
    entries = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "batch", AuditLog.entity_id == batch_id)
            .order_by(AuditLog.id)
        )
    ).all()
    return BatchDetailOut(
        **batch.model_dump(),
        movements=[
            MovementOut.model_validate(m).model_copy(
                update={"job_number": job_number, "user_full_name": full_name}
            )
            for m, job_number, full_name in movements
        ],
        audit=[AuditOut.model_validate(e) for e in entries],
    )
