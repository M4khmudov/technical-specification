import logging
import secrets
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Примеры секретов из репозитория (.env.example, прежние значения по умолчанию)
KNOWN_EXAMPLE_SECRETS = {
    "change-me-to-a-long-random-string-of-32-plus-chars",
}


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
    # LLM-судья эталонов — другая модель, чем отвечающая (по умолчанию другое семейство)
    eval_judge_model: str = "openai:gpt-5.4-mini"
    # Ожидание события, пока Code Interpreter выполняет код (он может молчать дольше)
    llm_code_timeout_seconds: float = Field(default=90.0, gt=0)

    # Секрет подписи токенов. Не задан или оставлен пример из репозитория — генерируется
    # случайный при запуске (токены сбрасываются при перезапуске): секрет, опубликованный
    # в репозитории, позволял бы подделать токен руководителя
    jwt_secret: SecretStr | None = Field(default=None, validate_default=True)

    @field_validator("jwt_secret")
    @classmethod
    def _real_secret(cls, value: SecretStr | None) -> SecretStr:
        raw = value.get_secret_value() if value else ""
        if len(raw) < 32 or raw in KNOWN_EXAMPLE_SECRETS:
            logger.warning("JWT_SECRET не задан или взят из примера — сгенерирован случайный")
            return SecretStr(secrets.token_hex(32))
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
