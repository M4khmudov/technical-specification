from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки читаются из переменных окружения (см. .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Candle Workshop"
    # Часовой пояс мастерской: от него считается «сегодня» (сервер живёт по UTC)
    timezone: str = "Europe/Moscow"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://candles:candles@localhost:5433/candles"
    redis_url: str = "redis://localhost:6379/0"

    # Мок-сервисы (курсы валют, маркировка)
    rates_service_url: str = "http://localhost:8001/rates"
    marking_service_url: str = "http://localhost:8001/marking"
    # Не ждём ответа дольше: медленный ответ сервис мог успеть обработать,
    # повтор с тем же ключом идемпотентности не создаст дубль
    marking_timeout_seconds: float = Field(default=5.0, gt=0)
    marking_max_attempts: int = Field(default=8, ge=1)
    marking_backoff_base_seconds: float = Field(default=5.0, gt=0)
    marking_backoff_max_seconds: float = Field(default=300.0, gt=0)
    # Ставить ли задачу в очередь arq сразу после завершения (в тестах выключено;
    # без очереди отчёт всё равно уйдёт — его подберёт периодическая задача воркера)
    marking_enqueue_enabled: bool = True

    # LLM: ключ выдаётся отдельно, в репозитории его нет
    openai_api_key: SecretStr | None = None
    # Groq — бесплатная альтернатива для проверки (LLM_PRIMARY=groq:openai/gpt-oss-120b)
    groq_api_key: SecretStr | None = None
    # Модели в формате «провайдер:модель»; запасная используется при 429/5xx/таймауте
    llm_primary: str = "openai:gpt-4.1"
    llm_fallback: str = "openai:gpt-4.1-mini"
    # Таймауты каждого вызова: ожидание очередного события (в т.ч. первого токена)
    # и весь ответ целиком
    llm_event_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    # Ожидание события, пока Code Interpreter выполняет код (он может молчать дольше)
    llm_code_timeout_seconds: float = Field(default=90.0, gt=0)

    jwt_secret: SecretStr = SecretStr("change-me-to-a-long-random-string-of-32-plus-chars")


@lru_cache
def get_settings() -> Settings:
    return Settings()
