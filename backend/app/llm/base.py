from collections.abc import AsyncIterator
from typing import Protocol

from app.llm.types import Event, LLMRequest


class LLMProvider(Protocol):
    """Провайдер LLM. Чтобы добавить нового (Anthropic, локальную модель и т.п.),
    достаточно реализовать этот протокол и зарегистрировать фабрику в registry.py.

    stream() отдаёт события ответа и бросает LLMError с признаком retryable.
    Событие Started выдаёт роутер — провайдеру его отдавать не нужно.
    """

    name: str

    def stream(self, request: LLMRequest, model: str) -> AsyncIterator[Event]: ...
