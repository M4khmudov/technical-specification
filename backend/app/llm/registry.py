"""Сборка роутера из настроек. Модель задаётся строкой «провайдер:модель»,
например LLM_PRIMARY=openai:gpt-4.1. Новый провайдер — новая запись в PROVIDERS."""

from collections.abc import Callable
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.router import LLMRouter, Route

PROVIDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "openai": lambda s: OpenAIProvider(
        s.openai_api_key.get_secret_value() if s.openai_api_key else None,
        timeout=s.llm_timeout_seconds,
    ),
}


def parse_model(spec: str) -> tuple[str, str]:
    provider, sep, model = spec.partition(":")
    if not sep:  # без префикса — OpenAI
        return "openai", spec
    if provider not in PROVIDERS:
        raise ValueError(f"Неизвестный провайдер LLM: {provider}")
    return provider, model


def build_router(settings: Settings) -> LLMRouter:
    instances: dict[str, LLMProvider] = {}
    routes = []
    for spec in (settings.llm_primary, settings.llm_fallback):
        if not spec:
            continue
        provider_name, model = parse_model(spec)
        if provider_name not in instances:
            instances[provider_name] = PROVIDERS[provider_name](settings)
        routes.append(Route(instances[provider_name], model))
    return LLMRouter(
        routes,
        event_timeout=settings.llm_event_timeout_seconds,
        total_timeout=settings.llm_timeout_seconds,
    )


@lru_cache
def get_llm() -> LLMRouter:
    """FastAPI-зависимость; в тестах подменяется заглушкой через dependency_overrides."""
    return build_router(get_settings())
