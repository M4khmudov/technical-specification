"""S10: безопасность стенда «по умолчанию»."""

import jwt
import pytest

from app.core.config import KNOWN_EXAMPLE_SECRETS, Settings
from app.core.errors import TooManyRequestsError
from app.core.throttle import LoginThrottle, login_throttle


@pytest.mark.parametrize("value", [None, "", "short", *KNOWN_EXAMPLE_SECRETS])
def test_example_or_missing_jwt_secret_is_replaced(value, monkeypatch):
    """Секрет из репозитория не используется: иначе любой подпишет токен руководителя."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    kwargs = {} if value is None else {"jwt_secret": value}
    secret = Settings(_env_file=None, **kwargs).jwt_secret.get_secret_value()
    assert len(secret) >= 32 and secret not in KNOWN_EXAMPLE_SECRETS


async def test_forged_token_is_not_accepted(client):
    forged = jwt.encode({"sub": "1"}, next(iter(KNOWN_EXAMPLE_SECRETS)), algorithm="HS256")
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_login_throttle_blocks_after_five_failures():
    throttle = LoginThrottle(max_failures=5, window=300)
    for _ in range(5):
        throttle.check("boss")
        throttle.failed("boss")
    with pytest.raises(TooManyRequestsError, match="Попробуйте через"):
        throttle.check("BOSS ")  # тот же логин в другом написании
    throttle.check("tech")  # другие логины не затронуты
    throttle.succeeded("boss")
    throttle.check("boss")


async def test_login_is_throttled_over_http(client):
    login = "throttled-user"
    try:
        for _ in range(5):
            response = await client.post("/api/auth/login", json={"login": login, "password": "x"})
            assert response.status_code == 401
        response = await client.post("/api/auth/login", json={"login": login, "password": "x"})
        assert response.status_code == 429
        assert "Слишком много неудачных попыток" in response.json()["detail"]
    finally:
        login_throttle.succeeded(login)
