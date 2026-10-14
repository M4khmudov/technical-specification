"""S1: «сегодня» — по времени мастерской (Москва), а не по UTC сервера."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.assistant.tool_set import this_week
from app.core import clock
from app.core.errors import DomainError
from app.models.enums import Currency
from app.schemas.production import JobCreateIn
from app.schemas.stock import ReceiptIn, ReceiptLineIn
from app.services import batches, production
from tests.factories import make_material, make_recipe, make_supplier, make_user
from tests.fakes import FakeRates

# Понедельник 19.10.2026, 01:30 по Москве — на сервере ещё воскресенье 18.10, 22:30 UTC
NIGHT = datetime(2026, 10, 18, 22, 30, tzinfo=UTC)


@pytest.fixture
def moscow_night(monkeypatch):
    monkeypatch.setattr(clock, "_utcnow", lambda: NIGHT)


def test_today_is_moscow_date(moscow_night):
    assert clock.today() == date(2026, 10, 19)
    assert this_week() == (date(2026, 10, 19), date(2026, 10, 25))


async def test_receipt_dated_today_is_not_in_the_future(session, moscow_night):
    user = await make_user(session)
    material = await make_material(session)
    supplier = await make_supplier(session)
    data = ReceiptIn(
        supplier_id=supplier.id,
        received_on=date(2026, 10, 19),
        lines=[
            ReceiptLineIn(material_id=material.id, quantity=1, price=1, currency=Currency.RUB)
        ],
    )
    created = await batches.create_receipt(session, user, data, FakeRates())
    assert created.batches[0].received_on == date(2026, 10, 19)


async def test_draft_with_past_date_cannot_be_planned(session, monkeypatch):
    user = await make_user(session)
    material = await make_material(session)
    recipe = await make_recipe(session, {material: "1"})
    draft = await production.create_job(
        session, user, JobCreateIn(recipe_id=recipe.id, quantity=1, planned_on=clock.today())
    )
    tomorrow = clock._utcnow() + timedelta(days=1)
    monkeypatch.setattr(clock, "_utcnow", lambda: tomorrow)
    with pytest.raises(DomainError, match="уже прошла"):
        await production.plan_job(session, user, draft.id)
