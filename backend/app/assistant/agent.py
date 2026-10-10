"""Агентный цикл одного хода диалога.

Модель отвечает текстом или просит вызвать инструменты. Инструменты выполняются
сразу (чтение) и их результаты возвращаются модели — пока она не ответит текстом
или не исчерпает лимит шагов. Наружу отдаются события для интерфейса.

Персональные данные (pii): в модель уходят только псевдонимы — вопрос и история
маскируются вызывающим, результаты инструментов — здесь. Аргументы, которые прислала
модель, распаковываются перед выполнением инструмента, а текст ответа — перед показом
пользователю (по ходу потока).
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.assistant.pii import Pseudonymizer, StreamUnmasker
from app.assistant.tools import ToolContext, ToolRegistry
from app.llm.router import LLMRouter
from app.llm.types import (
    Completed,
    Item,
    LLMRequest,
    Started,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolResult,
)

MAX_STEPS = 6
STEP_LIMIT_NOTE = "\n\n_Ответ не завершён: слишком много обращений к данным за один вопрос._"


@dataclass
class ChatEvent:
    type: str  # model | delta | tool | done | error
    data: dict[str, Any]


@dataclass
class TurnState:
    """Что накопилось за ход — сохраняется в сообщение при любом исходе."""

    text: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    fallback_from: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    unmasker: StreamUnmasker | None = None

    @property
    def content(self) -> str:
        return "".join(self.text)

    def flush(self) -> str:
        """Придержанный хвост ответа (начало псевдонима) — при обрыве тоже сохраняем."""
        tail = self.unmasker.flush() if self.unmasker else ""
        if tail:
            self.text.append(tail)
        return tail


async def run_turn(
    llm: LLMRouter,
    registry: ToolRegistry,
    ctx: ToolContext,
    instructions: str,
    items: list[Item],
    state: TurnState,
    pii: Pseudonymizer | None = None,
) -> AsyncIterator[ChatEvent]:
    items = list(items)
    if pii is not None:
        state.unmasker = pii.stream_unmasker()
    for _ in range(MAX_STEPS):
        calls: list[ToolCall] = []
        request = LLMRequest(instructions=instructions, items=items, tools=registry.specs())
        async for event in llm.stream(request):
            if isinstance(event, Started):
                state.model = event.model
                state.fallback_from = event.fallback_from
                yield ChatEvent(
                    "model",
                    {
                        "model": event.model,
                        "provider": event.provider,
                        "fallback_from": event.fallback_from,
                    },
                )
            elif isinstance(event, TextDelta):
                text = state.unmasker.feed(event.text) if state.unmasker else event.text
                if text:
                    state.text.append(text)
                    yield ChatEvent("delta", {"text": text})
            elif isinstance(event, ToolCallRequested):
                calls.append(event.call)
            elif isinstance(event, Completed):
                state.input_tokens += event.input_tokens or 0
                state.output_tokens += event.output_tokens or 0
        tail = state.flush()
        if tail:
            yield ChatEvent("delta", {"text": tail})
        if not calls:
            return

        for call in calls:
            yield ChatEvent("tool", {"name": call.name, "status": "started"})
            # Модель видела псевдонимы — в сервис уходят настоящие имена
            real_call = (
                ToolCall(call.call_id, call.name, pii.unmask(call.arguments)) if pii else call
            )
            outcome, arguments = await registry.execute(real_call, ctx)
            record = {
                "name": call.name,
                "arguments": arguments,
                "ok": outcome.ok,
                "summary": outcome.summary,
                **outcome.extra,
            }
            state.tools.append(record)
            yield ChatEvent("tool", {**record, "status": "finished"})
            output = json.dumps(outcome.output, ensure_ascii=False, default=str)
            items.append(call)  # в истории для модели — как она его прислала (с псевдонимами)
            items.append(ToolResult(call.call_id, pii.mask(output) if pii else output))

    state.text.append(STEP_LIMIT_NOTE)
    yield ChatEvent("delta", {"text": STEP_LIMIT_NOTE})
