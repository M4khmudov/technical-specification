"""Нейтральные типы LLM-слоя — не зависят от конкретного провайдера.

Диалог — список элементов (сообщения, вызовы инструментов, их результаты).
Ответ модели — поток событий. Провайдер переводит их в свой формат и обратно.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------- элементы диалога


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str


@dataclass(frozen=True)
class ToolCall:
    """Модель просит вызвать функцию нашего сервисного слоя."""

    call_id: str
    name: str
    arguments: str  # JSON-строка как пришла от модели — валидируется отдельно


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    output: str  # JSON-строка с результатом для модели


Item = UserMessage | AssistantMessage | ToolCall | ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema аргументов


@dataclass(frozen=True)
class CodeInterpreterSpec:
    """Встроенный инструмент провайдера: выполнение кода в изолированном контейнере."""

    file_ids: tuple[str, ...] = ()


@dataclass
class LLMRequest:
    instructions: str  # системный промпт
    items: list[Item]
    tools: list[ToolSpec] = field(default_factory=list)
    code_interpreter: CodeInterpreterSpec | None = None
    max_output_tokens: int | None = 2000


# ---------------------------------------------------------------- события ответа


@dataclass(frozen=True)
class Started:
    """Первое событие: какая модель отвечает (после возможного перехода на запасную)."""

    provider: str
    model: str
    fallback_from: str | None = None  # если основная модель была недоступна


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallRequested:
    call: ToolCall


@dataclass(frozen=True)
class CodeExecuted:
    """Code Interpreter выполнил код. outputs — логи (type=logs) и картинки (type=image)."""

    code: str
    container_id: str | None = None
    outputs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class FileCited:
    """Модель сослалась в ответе на файл, созданный в контейнере Code Interpreter."""

    container_id: str
    file_id: str
    filename: str


@dataclass(frozen=True)
class Completed:
    model: str
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish: Literal["stop", "tool_calls", "length"] = "stop"


@dataclass(frozen=True)
class Heartbeat:
    """Провайдер жив, но показывать нечего (служебное событие потока). slow — началась
    долгая операция без событий (Code Interpreter выполняет код): ждём дольше."""

    slow: bool = False


Event = Started | TextDelta | ToolCallRequested | CodeExecuted | FileCited | Completed | Heartbeat
