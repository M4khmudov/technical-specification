"""Права ролей (на уровне сервисов и HTTP), журнал изменений, понятные ошибки."""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.errors import NotFoundError, PermissionDeniedError
from app.models import ProductionJob
from app.models.enums import Currency, Role
from app.schemas.production import JobCompleteIn, JobCreateIn
from app.schemas.stock import ReceiptIn, ReceiptLineIn
from app.services import audit
from app.services import batches as batch_service
from app.services import production as job_service
from tests.factories import make_material, make_recipe, make_user
from tests.fakes import FakeRates

MISSING = 999_999
FUTURE = date.today() + timedelta(days=2)

# Операция → вызов с несуществующими id. Если право есть, сервис дойдёт до поиска
# и ответит «не найдено»; если нет — «Недостаточно прав» раньше любых чтений.
OPERATIONS = {
    "receipt": lambda s, u: batch_service.create_receipt(
        s,
        u,
        ReceiptIn(
            supplier_id=MISSING,
            received_on=date(2026, 10, 1),
            lines=[ReceiptLineIn(material_id=1, quantity=1, price=1, currency=Currency.RUB)],
        ),
        FakeRates(),
    ),
    "accept": lambda s, u: batch_service.accept_batch(s, u, MISSING),
    "reject": lambda s, u: batch_service.reject_batch(s, u, MISSING),
    "create_job": lambda s, u: job_service.create_job(
        s, u, JobCreateIn(recipe_id=MISSING, quantity=1, planned_on=FUTURE)
    ),
    "plan": lambda s, u: job_service.plan_job(s, u, MISSING),
    "reserve": lambda s, u: job_service.reserve_job(s, u, MISSING),
    "start": lambda s, u: job_service.start_job(s, u, MISSING),
    "complete": lambda s, u: job_service.complete_job(s, u, MISSING, JobCompleteIn()),
    "cancel": lambda s, u: job_service.cancel_job(s, u, MISSING),
}

ALLOWED = {
    Role.STOREKEEPER: {"receipt", "accept", "reject"},
    Role.TECHNOLOGIST: {"create_job", "plan", "reserve", "start", "complete", "cancel"},
    Role.MANAGER: set(OPERATIONS),
}


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("operation", list(OPERATIONS))
async def test_role_matrix(session, role, operation):
    user = await make_user(session, role=role)
    expected = NotFoundError if operation in ALLOWED[role] else PermissionDeniedError
    with pytest.raises(expected):
        await OPERATIONS[operation](session, user)


async def test_denied_action_explains_who_can_do_it(session):
    user = await make_user(session, role=Role.STOREKEEPER)
    with pytest.raises(PermissionDeniedError) as exc:
        await job_service.start_job(session, user, MISSING)
    assert exc.value.message == (
        "Недостаточно прав: роль «Кладовщик» не может запускать и завершать задания. "
        "Это может Технолог или Руководитель."
    )


async def test_denied_action_changes_nothing(session):
    storekeeper = await make_user(session, role=Role.STOREKEEPER)
    material = await make_material(session)
    recipe = await make_recipe(session, {material: "1"})
    before = await session.scalar(select(func.count()).select_from(ProductionJob))

    with pytest.raises(PermissionDeniedError):
        await job_service.create_job(
            session,
            storekeeper,
            JobCreateIn(recipe_id=recipe.id, quantity=5, planned_on=FUTURE),
            plan=True,
        )
    assert await session.scalar(select(func.count()).select_from(ProductionJob)) == before


@pytest.mark.parametrize("role", [Role.STOREKEEPER, Role.TECHNOLOGIST])
async def test_only_manager_sees_full_audit(session, role):
    user = await make_user(session, role=role)
    with pytest.raises(PermissionDeniedError):
        await audit.list_entries(session, user)


async def test_audit_records_who_when_before_after(session):
    manager = await make_user(session, role=Role.MANAGER, full_name="Кузнецова Анна Игоревна")
    material = await make_material(session)
    recipe = await make_recipe(session, {material: "1"})
    job = await job_service.create_job(
        session, manager, JobCreateIn(recipe_id=recipe.id, quantity=5, planned_on=FUTURE)
    )
    await job_service.cancel_job(session, manager, job.id)

    entries, total = await audit.list_entries(session, manager, entity_type="job", entity_id=job.id)
    assert total == 2
    cancel = entries[0]  # новые сверху
    assert cancel.action == "cancel"
    assert cancel.user_full_name == "Кузнецова Анна Игоревна"
    assert cancel.before["status"] == "draft"
    assert cancel.after == {"status": "cancelled"}
    assert cancel.created_at is not None


# ---------------------------------------------------------------- HTTP


async def test_http_forbidden_is_403_with_text(api):
    client, *_ = api  # Кладовщик
    response = await client.post(
        "/api/jobs", json={"recipe_id": 1, "quantity": 1, "planned_on": FUTURE.isoformat()}
    )
    assert response.status_code == 403
    assert response.json()["detail"].startswith("Недостаточно прав: роль «Кладовщик»")

    audit_response = await client.get("/api/audit")
    assert audit_response.status_code == 403


async def test_me_lists_permissions(api):
    client, *_ = api
    me = (await client.get("/api/auth/me")).json()
    assert me["role_label"] == "Кладовщик"
    assert me["permissions"] == ["batch.review", "receipt.create"]


async def test_validation_errors_are_human_readable(api):
    client, material, supplier = api
    response = await client.post(
        "/api/receipts",
        json={
            "supplier_id": supplier.id,
            "received_on": "10.10.2026",
            "lines": [
                {"material_id": material.id, "quantity": "0", "price": "abc", "currency": "GBP"}
            ],
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"].startswith("Проверьте данные.")
    assert "Дата поступления: некорректная дата, нужен формат ГГГГ-ММ-ДД" in body["errors"]
    assert "Строки поступления №1 → Количество: должно быть больше 0" in body["errors"]
    assert "Строки поступления №1 → Цена: должно быть числом" in body["errors"]
    assert any(e.startswith("Строки поступления №1 → Валюта: допустимые") for e in body["errors"])


async def test_unexpected_error_is_not_leaked():
    from app.main import create_app

    app = create_app()

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("секретные подробности: password=123")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Внутренняя ошибка сервера. Попробуйте позже."}
    assert "password" not in response.text
