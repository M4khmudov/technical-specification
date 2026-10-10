"""Основная и запасная модели за одним интерфейсом.

- Пробуем модели по порядку. 429, 5xx, таймаут, сетевая ошибка → следующая модель.
  Прочие 4xx — ошибка самого запроса: не повторяем, сразу отдаём ошибку.
- У каждого вызова есть таймаут: на ожидание очередного события (первого токена в том
  числе) и на весь ответ целиком.
- Переход возможен только до первого события. Если ответ оборвался посередине —
  LLMInterruptedError: часть ответа пользователь уже видел, подменять её нельзя.
- В лог пишем модель, длительность, токены и исход — без текста запроса и ответа.
"""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.llm.base import FileStore, LLMProvider
from app.llm.errors import LLMError, LLMInterruptedError, LLMUnavailableError
from app.llm.types import Completed, Event, LLMRequest, Started, TextDelta

logger = logging.getLogger(__name__)

UNAVAILABLE = "Ассистент сейчас недоступен: языковая модель не отвечает. Попробуйте через минуту."
BAD_REQUEST = "Ассистент не смог обработать запрос. Попробуйте сформулировать его иначе."
NOT_CONFIGURED = "Ассистент не настроен: не задан ключ {key}."
INTERRUPTED = "Ответ прервался из-за сбоя языковой модели. Попробуйте повторить вопрос."


@dataclass(frozen=True)
class Route:
    provider: LLMProvider
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider.name}:{self.model}"


class LLMRouter:
    def __init__(self, routes: list[Route], *, event_timeout: float, total_timeout: float):
        if not routes:
            raise ValueError("нужна хотя бы одна модель")
        self.routes = routes
        self.event_timeout = event_timeout
        self.total_timeout = total_timeout

    async def stream(self, request: LLMRequest) -> AsyncIterator[Event]:
        errors: list[LLMError] = []
        for index, route in enumerate(self.routes):
            started_at = time.monotonic()
            deadline = started_at + self.total_timeout
            events = route.provider.stream(request, route.model).__aiter__()
            started = False
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LLMError("total timeout", retryable=True)
                    try:
                        event = await asyncio.wait_for(
                            anext(events), timeout=min(self.event_timeout, remaining)
                        )
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        raise LLMError("timeout waiting for model", retryable=True) from exc
                    if not started:
                        started = True
                        yield Started(
                            provider=route.provider.name,
                            model=route.model,
                            fallback_from=self.routes[0].label if index > 0 else None,
                        )
                    if isinstance(event, Completed):
                        logger.info(
                            "llm ok model=%s ms=%d in=%s out=%s finish=%s",
                            route.label,
                            (time.monotonic() - started_at) * 1000,
                            event.input_tokens,
                            event.output_tokens,
                            event.finish,
                        )
                    yield event
                return
            except LLMError as exc:
                logger.warning(
                    "llm error model=%s retryable=%s started=%s: %s",
                    route.label,
                    exc.retryable,
                    started,
                    exc.message,
                )
                if started:
                    raise LLMInterruptedError(INTERRUPTED, exc) from exc
                errors.append(exc)
                if not exc.retryable:
                    key = re.search(r"\b([A-Z_]+_API_KEY) не задан", exc.message)
                    message = NOT_CONFIGURED.format(key=key.group(1)) if key else BAD_REQUEST
                    raise LLMUnavailableError(message, errors) from exc
            finally:
                await _close(events)
        raise LLMUnavailableError(UNAVAILABLE, errors)

    @property
    def files(self) -> "FileStore | None":
        """Хранилище файлов для Code Interpreter — у основного провайдера.

        Основная и запасная модели здесь у одного провайдера, поэтому file_id подходят
        обеим. Если запасная будет у другого провайдера, Code Interpreter у неё недоступен.
        """
        provider = self.routes[0].provider
        if not hasattr(provider, "upload_file") or not getattr(provider, "supports_files", True):
            return None
        return provider  # type: ignore[return-value]

    @property
    def code_interpreter(self) -> bool:
        """Есть ли у основного провайдера выполнение кода (у Groq — без файлов)."""
        return bool(getattr(self.routes[0].provider, "supports_code_interpreter", True))

    async def complete(self, request: LLMRequest) -> tuple[str, Started | None]:
        """Ответ целиком (для служебных вызовов: LLM-судья, извлечение фактов)."""
        parts: list[str] = []
        started: Started | None = None
        async for event in self.stream(request):
            if isinstance(event, Started):
                started = event
            elif isinstance(event, TextDelta):
                parts.append(event.text)
        return "".join(parts), started


async def _close(events: AsyncIterator[Event]) -> None:
    aclose = getattr(events, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:  # noqa: BLE001 — закрытие брошенного потока не должно ронять ответ
            logger.debug("failed to close llm stream", exc_info=True)
