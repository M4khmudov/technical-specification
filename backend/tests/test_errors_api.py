"""B3: на «кривой» ввод API отвечает понятным текстом, а не 500.

Проход по всем эндпоинтам из OpenAPI: идентификаторы за пределами int32, символ NUL
в тексте, цена, которая переполняет себестоимость в рублях, ответы самого фреймворка."""

import re
from datetime import date

import pytest

from app.main import app
from app.models.enums import Role

BIG = 2**31  # 2147483648 — уже не помещается в integer PostgreSQL


@pytest.fixture
def api_role():
    return Role.MANAGER  # все права — чтобы дойти до базы, а не до 403


def _endpoints() -> list[tuple[str, str]]:
    result = []
    for path, methods in app.openapi()["paths"].items():
        for method in methods:
            result.append((method.upper(), path))
    return result


def _fill(path: str, value) -> str:
    return re.sub(r"\{[^}]+\}", str(value), path)


@pytest.mark.parametrize(("method", "path"), [e for e in _endpoints() if "{" in e[1]])
async def test_huge_ids_never_give_500(api, method, path):
    client, *_ = api
    response = await client.request(method, _fill(path, BIG), json={})
    assert response.status_code < 500, (method, path, response.text)
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize(
    "params",
    [{"page": BIG}, {"size": BIG}, {"material_id": BIG}, {"supplier_id": BIG}],
)
async def test_huge_query_numbers_never_give_500(api, params):
    client, *_ = api
    for path in ["/api/batches", "/api/jobs", "/api/audit"]:
        response = await client.get(path, params=params)
        assert response.status_code < 500, (path, params, response.text)


async def test_nul_byte_in_text_is_rejected_with_text(api):
    client, material, supplier = api
    response = await client.post(
        "/api/receipts",
        json={
            "supplier_id": supplier.id,
            "received_on": date(2026, 10, 7).isoformat(),
            "lines": [
                {
                    "material_id": material.id,
                    "quantity": "1",
                    "price": "1",
                    "currency": "RUB",
                    "comment": "партия\u0000",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "недопустимый символ" in response.json()["detail"]


async def test_nul_byte_in_chat_message(api):
    client, *_ = api
    conversation = (await client.post("/api/chat/conversations", json={})).json()
    response = await client.post(
        f"/api/chat/conversations/{conversation['id']}/messages", json={"text": "привет\u0000"}
    )
    assert response.status_code == 422
    assert "недопустимый символ" in response.json()["detail"]


async def test_price_that_overflows_cost_is_a_clear_error(api):
    client, material, supplier = api
    response = await client.post(
        "/api/receipts",
        json={
            "supplier_id": supplier.id,
            "received_on": date(2026, 10, 7).isoformat(),
            "lines": [
                {
                    "material_id": material.id,
                    "quantity": "1",
                    "price": "200000000",
                    "currency": "USD",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "Цена" in response.json()["detail"]


async def test_framework_errors_are_in_russian(api):
    client, *_ = api
    missing = await client.get("/api/no-such-thing")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Адрес не найден"}
    wrong_method = await client.delete("/api/batches")
    assert wrong_method.status_code == 405
    assert wrong_method.json() == {"detail": "Этот метод для адреса не поддерживается"}
