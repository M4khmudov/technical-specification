"""HTTP-слой партий: вход, понятные ошибки, сквозной сценарий поступления."""

from datetime import date


async def test_requires_login(client):
    response = await client.get("/api/batches")
    assert response.status_code == 401
    assert response.json() == {"detail": "Требуется вход в систему"}


async def test_wrong_password(client):
    response = await client.post("/api/auth/login", json={"login": "nobody", "password": "x"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Неверный логин или пароль"


async def test_receipt_accept_and_conflict_message(api):
    client, material, supplier = api
    response = await client.post(
        "/api/receipts",
        json={
            "supplier_id": supplier.id,
            "received_on": date(2026, 10, 7).isoformat(),
            "lines": [
                {"material_id": material.id, "quantity": "12.5", "price": "3", "currency": "USD"}
            ],
        },
    )
    assert response.status_code == 201
    batch = response.json()["batches"][0]
    assert batch["status"] == "pending"
    assert batch["unit_cost_rub"] == "270.000000"  # Decimal уходит строкой, без float

    accepted = await client.post(f"/api/batches/{batch['id']}/accept")
    assert accepted.json()["status"] == "accepted"

    again = await client.post(f"/api/batches/{batch['id']}/accept")
    assert again.status_code == 409
    assert "только у партии «На проверке»" in again.json()["detail"]

    card = await client.get(f"/api/batches/{batch['id']}")
    assert [a["user_full_name"] for a in card.json()["audit"]] == ["Сидоров Пётр"] * 2

    registry = await client.get(
        "/api/batches", params={"material": material.code, "sort": "free_qty", "order": "asc"}
    )
    assert registry.json()["total"] == 1


async def test_unknown_batch_is_404_with_text(api):
    client, *_ = api
    response = await client.get("/api/batches/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Партия #999999 не найдена"}
