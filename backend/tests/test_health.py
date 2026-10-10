async def test_health_checks_database(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_openapi_docs_available(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
