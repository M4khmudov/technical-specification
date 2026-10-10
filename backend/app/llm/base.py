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


class FileStore(Protocol):
    """Файлы для Code Interpreter у провайдера: загрузить наши данные в контейнер,
    забрать файлы, которые создал код модели."""

    async def upload_file(self, filename: str, data: bytes) -> str: ...

    async def delete_file(self, file_id: str) -> None: ...

    async def list_generated_files(self, container_id: str) -> list[tuple[str, str]]:
        """[(file_id, путь)] файлов, созданных кодом модели (не загруженных нами)."""
        ...

    async def download_container_file(self, container_id: str, file_id: str) -> bytes: ...
