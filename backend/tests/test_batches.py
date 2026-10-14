"""Поступления и партии: себестоимость в рублях, статусы, реестр, история."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core import clock
from app.core.errors import ConflictError, DomainError, ExternalServiceError, NotFoundError
from app.models import AuditLog, Batch, BatchMovement
from app.models.enums import BatchStatus, Currency, MovementKind
from app.schemas.stock import BatchFilter, BatchSort, ReceiptIn, ReceiptLineIn
from app.services import batches as service
from tests.factories import make_material, make_supplier, make_user
from tests.fakes import FakeRates

DAY = date(2026, 10, 7)


def line(material, qty="10", price="100", currency=Currency.RUB, comment=None) -> ReceiptLineIn:
    return ReceiptLineIn(
        material_id=material.id,
        quantity=Decimal(qty),
        price=Decimal(price),
        currency=currency,
        comment=comment,
    )


async def test_receipt_lines_become_pending_batches_with_rub_cost(session):
    user = await make_user(session, full_name="Иванова Мария Петровна")
    supplier = await make_supplier(session)
    wax, wick, oil = [await make_material(session) for _ in range(3)]
    data = ReceiptIn(
        supplier_id=supplier.id,
        received_on=DAY,
        lines=[
            line(wax, "25.5", "180.50", Currency.RUB),
            line(wick, "1000", "0.12", Currency.USD),
            line(oil, "2.25", "41.333333", Currency.EUR),
        ],
    )
    rates = FakeRates({Currency.USD: "92.4567", Currency.EUR: "100.1234"})

    receipt = await service.create_receipt(session, user, data, rates)

    by_material = {b.material_id: b for b in receipt.batches}
    assert {b.status for b in receipt.batches} == {BatchStatus.PENDING}
    assert len({b.number for b in receipt.batches}) == 3  # у каждой партии свой номер
    assert by_material[wax.id].unit_cost_rub == Decimal("180.500000")
    assert by_material[wick.id].unit_cost_rub == Decimal("11.094804")  # 0.12 × 92.4567
    # 41.333333 × 100.1234 = 4138.4338332922 → округление до 6 знаков
    assert by_material[oil.id].unit_cost_rub == Decimal("4138.433833")
    assert by_material[wick.id].exchange_rate == Decimal("92.4567")
    assert by_material[wax.id].free_qty == Decimal("25.5")
    # Курс запрашивается один раз на валюту
    assert sorted(c for c, _ in rates.calls) == [Currency.EUR, Currency.RUB, Currency.USD]

    movements = (await session.scalars(select(BatchMovement))).all()
    assert {(m.kind, m.quantity) for m in movements} >= {(MovementKind.RECEIPT, Decimal("25.5"))}
    log = await session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "batch",
            AuditLog.entity_id == by_material[wax.id].id,
            AuditLog.action == "create",
        )
    )
    assert log.user_full_name == "Иванова Мария Петровна"
    assert Decimal(log.after["quantity"]) == Decimal("25.5")


async def test_receipt_stores_date_of_rate_actually_used(session):
    """Если сервис отдал курс за пятницу на субботнее поступление — это видно в партии."""
    user = await make_user(session)
    supplier = await make_supplier(session)
    material = await make_material(session)

    class FridayRates(FakeRates):
        async def get_rate(self, currency, on):
            quote = await super().get_rate(currency, on)
            return type(quote)(quote.currency, quote.rate, date(2026, 10, 9))

    saturday = date(2026, 10, 10)
    data = ReceiptIn(
        supplier_id=supplier.id,
        received_on=saturday,
        lines=[line(material, currency=Currency.USD)],
    )
    receipt = await service.create_receipt(session, user, data, FridayRates())
    assert receipt.batches[0].received_on == saturday
    assert receipt.batches[0].rate_date == date(2026, 10, 9)


async def _batch_count(session) -> int:
    return await session.scalar(select(func.count()).select_from(Batch))


async def test_future_receipt_rejected(session):
    user = await make_user(session)
    supplier = await make_supplier(session)
    material = await make_material(session)
    data = ReceiptIn(
        supplier_id=supplier.id,
        received_on=clock.today() + timedelta(days=1),
        lines=[line(material)],
    )
    with pytest.raises(DomainError, match="в будущем"):
        await service.create_receipt(session, user, data, FakeRates())


async def test_unknown_material_creates_nothing(session):
    user = await make_user(session)
    supplier = await make_supplier(session)
    before = await _batch_count(session)
    data = ReceiptIn(
        supplier_id=supplier.id,
        received_on=DAY,
        lines=[ReceiptLineIn(material_id=999_999, quantity=1, price=1, currency=Currency.RUB)],
    )
    with pytest.raises(NotFoundError, match="Материал #999999 не найден"):
        await service.create_receipt(session, user, data, FakeRates())
    assert await _batch_count(session) == before


async def test_rates_service_down_creates_nothing(session):
    user = await make_user(session)
    supplier = await make_supplier(session)
    material = await make_material(session)
    before = await _batch_count(session)
    data = ReceiptIn(
        supplier_id=supplier.id, received_on=DAY, lines=[line(material, currency=Currency.USD)]
    )
    with pytest.raises(ExternalServiceError):
        await service.create_receipt(session, user, data, FakeRates(fail=True))
    assert await _batch_count(session) == before


@pytest.mark.parametrize("qty", ["0", "-5"])
def test_non_positive_quantity_is_invalid(qty):
    with pytest.raises(ValueError):
        ReceiptLineIn(material_id=1, quantity=Decimal(qty), price=1, currency=Currency.RUB)


async def _pending_batch(session, user):
    supplier = await make_supplier(session)
    material = await make_material(session)
    data = ReceiptIn(supplier_id=supplier.id, received_on=DAY, lines=[line(material)])
    return (await service.create_receipt(session, user, data, FakeRates())).batches[0]


async def test_accept_pending_batch(session):
    user = await make_user(session)
    batch = await _pending_batch(session, user)

    accepted = await service.accept_batch(session, user, batch.id)

    assert accepted.status == BatchStatus.ACCEPTED
    log = await session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "batch",
            AuditLog.entity_id == batch.id,
            AuditLog.action == "accept",
        )
    )
    assert log.before == {"status": "pending"}
    assert log.after == {"status": "accepted"}


async def test_reject_pending_batch_with_reason(session):
    user = await make_user(session)
    batch = await _pending_batch(session, user)

    rejected = await service.reject_batch(session, user, batch.id, "Воск с примесями")

    assert rejected.status == BatchStatus.REJECTED
    history = await service.get_batch_history(session, batch.id)
    assert history.audit[-1].after == {"status": "rejected", "reason": "Воск с примесями"}


@pytest.mark.parametrize("first", ["accept_batch", "reject_batch"])
async def test_status_changes_only_from_pending(session, first):
    user = await make_user(session)
    batch = await _pending_batch(session, user)
    await getattr(service, first)(session, user, batch.id)

    with pytest.raises(ConflictError, match="только у партии «На проверке»"):
        await service.accept_batch(session, user, batch.id)
    with pytest.raises(ConflictError):
        await service.reject_batch(session, user, batch.id)


async def test_unknown_batch(session):
    user = await make_user(session)
    with pytest.raises(NotFoundError, match="Партия #999999 не найдена"):
        await service.accept_batch(session, user, 999_999)


async def test_registry_filters_sorting_and_pagination(session):
    user = await make_user(session)
    supplier = await make_supplier(session, name="ООО «Восковой двор» реестр")
    soy = await make_material(session, code="WAX-SOY-R", name="Воск соевый реестр")
    wick = await make_material(session, code="WICK-R", name="Фитиль реестр")
    data = ReceiptIn(
        supplier_id=supplier.id,
        received_on=DAY,
        lines=[line(soy, "5"), line(soy, "50"), line(soy, "20"), line(wick, "100")],
    )
    await service.create_receipt(session, user, data, FakeRates())

    page = await service.list_batches(
        session,
        BatchFilter(material="соевый РЕЕСТР"),  # поиск без учёта регистра
        sort=BatchSort.FREE_QTY,
        descending=True,
        page=1,
        size=2,
    )
    assert page.total == 3
    assert [b.free_qty for b in page.items] == [Decimal("50"), Decimal("20")]

    second = await service.list_batches(
        session, BatchFilter(material="WAX-SOY-R"), BatchSort.FREE_QTY, True, page=2, size=2
    )
    assert [b.free_qty for b in second.items] == [Decimal("5")]

    by_supplier = await service.list_batches(session, BatchFilter(supplier="восковой двор» реестр"))
    assert by_supplier.total == 4
    assert by_supplier.items[0].supplier_name == "ООО «Восковой двор» реестр"

    accepted = await service.list_batches(
        session, BatchFilter(material_id=wick.id, status=BatchStatus.ACCEPTED)
    )
    assert accepted.total == 0


async def test_registry_search_treats_wildcards_literally(session):
    user = await make_user(session)
    supplier = await make_supplier(session)
    material = await make_material(session, name="Отдушка 100% лаванда")
    other = await make_material(session, name="Отдушка 1000 роза")
    data = ReceiptIn(supplier_id=supplier.id, received_on=DAY, lines=[line(material), line(other)])
    await service.create_receipt(session, user, data, FakeRates())

    page = await service.list_batches(session, BatchFilter(material="100%"))
    assert [b.material_name for b in page.items] == ["Отдушка 100% лаванда"]


async def test_batch_history_shows_receipt_and_status_changes(session):
    user = await make_user(session, full_name="Петров Иван Сергеевич")
    batch = await _pending_batch(session, user)
    await service.accept_batch(session, user, batch.id)

    history = await service.get_batch_history(session, batch.id)

    assert [m.kind for m in history.movements] == [MovementKind.RECEIPT]
    assert history.movements[0].user_full_name == "Петров Иван Сергеевич"
    assert [a.action for a in history.audit] == ["create", "accept"]
