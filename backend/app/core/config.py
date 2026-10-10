from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки читаются из переменных окружения (см. .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Candle Workshop"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://candles:candles@localhost:5433/candles"
    redis_url: str = "redis://localhost:6379/0"

    # Мок-сервисы (курсы валют, маркировка)
    rates_service_url: str = "http://localhost:8001/rates"
    marking_service_url: str = "http://localhost:8001/marking"

    # LLM: ключ выдаётся отдельно, в репозитории его нет
    openai_api_key: SecretStr | None = None
    openai_model_primary: str = "gpt-4.1"
    openai_model_fallback: str = "gpt-4.1-mini"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)

    jwt_secret: SecretStr = SecretStr("change-me-to-a-long-random-string-of-32-plus-chars")


@lru_cache
def get_settings() -> Settings:
    return Settings()
