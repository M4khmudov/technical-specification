"""Агентный цикл одного хода диалога.

Модель отвечает текстом или просит вызвать инструменты. Инструменты выполняются
сразу (чтение) и их результаты возвращаются модели — пока она не ответит текстом
или не исчерпает лимит шагов. Наружу отдаются события для интерфейса.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

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

    @property
    def content(self) -> str:
        return "".join(self.text)


async def run_turn(
    llm: LLMRouter,
    registry: ToolRegistry,
    ctx: ToolContext,
    instructions: str,
    items: list[Item],
    state: TurnState,
) -> AsyncIterator[ChatEvent]:
    items = list(items)
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
                state.text.append(event.text)
                yield ChatEvent("delta", {"text": event.text})
            elif isinstance(event, ToolCallRequested):
                calls.append(event.call)
            elif isinstance(event, Completed):
                state.input_tokens += event.input_tokens or 0
                state.output_tokens += event.output_tokens or 0
        if not calls:
            return

        for call in calls:
            yield ChatEvent("tool", {"name": call.name, "status": "started"})
            outcome, arguments = await registry.execute(call, ctx)
            record = {
                "name": call.name,
                "arguments": arguments,
                "ok": outcome.ok,
                "summary": outcome.summary,
                **outcome.extra,
            }
            state.tools.append(record)
            yield ChatEvent("tool", {**record, "status": "finished"})
            items.append(call)
            items.append(
                ToolResult(
                    call.call_id, json.dumps(outcome.output, ensure_ascii=False, default=str)
                )
            )

    state.text.append(STEP_LIMIT_NOTE)
    yield ChatEvent("delta", {"text": STEP_LIMIT_NOTE})
