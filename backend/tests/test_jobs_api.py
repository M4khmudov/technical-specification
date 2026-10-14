"""HTTP-слой заданий: сквозной сценарий и понятные ошибки."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.core import clock
from app.models import Batch, Receipt, Recipe, RecipeItem
from app.models.enums import BatchStatus, Currency, Role


@pytest.fixture
def api_role():
    return Role.TECHNOLOGIST


async def test_job_lifecycle_over_http(api, session_factory):
    client, material, supplier = api
    me = (await client.get("/api/auth/me")).json()
    async with session_factory() as s:
        recipe = Recipe(
            name=f"Лаванда {uuid4().hex[:6]}",
            items=[RecipeItem(material_id=material.id, qty_per_unit="0.25")],
        )
        receipt = Receipt(
            supplier_id=supplier.id, received_on=date(2026, 9, 1), created_by_id=me["id"]
        )
        s.add_all([recipe, receipt])
        await s.flush()
        s.add(
            Batch(
                receipt_id=receipt.id,
                material_id=material.id,
                received_on=receipt.received_on,
                quantity=40,
                remaining_qty=40,
                price=500,
                currency=Currency.RUB,
                exchange_rate=1,
                rate_date=receipt.received_on,
                unit_cost_rub=500,
                status=BatchStatus.ACCEPTED,
            )
        )
        await s.commit()

    planned_on = (clock.today() + timedelta(days=4)).isoformat()
    created = await client.post(
        "/api/jobs",
        params={"plan": "true"},
        json={"recipe_id": recipe.id, "quantity": 200, "planned_on": planned_on},
    )
    assert created.status_code == 201
    job = created.json()
    line = job["materials"][0]
    assert (line["required_qty"], line["reserved_qty"], line["deficit_qty"]) == (
        "50.000",
        "40.000",
        "10.000",
    )
    assert line["light"] == "yellow"

    start = await client.post(f"/api/jobs/{job['id']}/start")
    assert start.status_code == 400
    assert start.json()["detail"].startswith(f"Нельзя запустить задание №{job['number']}")

    cancelled = await client.post(f"/api/jobs/{job['id']}/cancel")
    assert cancelled.json()["status_label"] == "Отменено"

    again = await client.post(f"/api/jobs/{job['id']}/cancel")
    assert again.status_code == 409


async def test_invalid_quantity_is_rejected(api):
    client, *_ = api
    response = await client.post(
        "/api/jobs", json={"recipe_id": 1, "quantity": 0, "planned_on": "2030-01-01"}
    )
    assert response.status_code == 422
