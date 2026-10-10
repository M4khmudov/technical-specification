"""Заглушка провайдера для тестов: отвечает по сценарию, в сеть не ходит.

Каждый вызов stream() берёт следующий сценарий из очереди. Сценарий — список
событий и/или исключений (LLMError), которые выдаются по порядку. Задержка
(«sleep») позволяет проверить таймауты.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.llm.types import Completed, Event, LLMRequest, TextDelta, ToolCall, ToolCallRequested


@dataclass(frozen=True)
class Sleep:
    seconds: float


Step = Event | Exception | Sleep


class ScriptedProvider:
    def __init__(self, *scripts: list[Step], name: str = "fake"):
        self.name = name
        self.scripts = list(scripts)
        self.requests: list[tuple[str, LLMRequest]] = []

    def push(self, *steps: Step) -> None:
        self.scripts.append(list(steps))

    async def stream(self, request: LLMRequest, model: str) -> AsyncIterator[Event]:
        self.requests.append((model, request))
        if not self.scripts:
            raise AssertionError("ScriptedProvider: сценарии закончились")
        for step in self.scripts.pop(0):
            if isinstance(step, Sleep):
                await asyncio.sleep(step.seconds)
            elif isinstance(step, Exception):
                raise step
            else:
                yield step


def text_reply(text: str, model: str = "fake-model") -> list[Step]:
    """Сценарий «модель ответила текстом» (по кусочкам, как при стриминге)."""
    words = text.split(" ")
    deltas: list[Step] = [TextDelta(w if i == 0 else " " + w) for i, w in enumerate(words)]
    return [*deltas, Completed(model=model, input_tokens=10, output_tokens=len(words))]


def tool_call_reply(name: str, arguments: str, call_id: str = "call_1", model: str = "fake-model"):
    """Сценарий «модель просит вызвать инструмент»."""
    return [
        ToolCallRequested(ToolCall(call_id, name, arguments)),
        Completed(model=model, finish="tool_calls"),
    ]
