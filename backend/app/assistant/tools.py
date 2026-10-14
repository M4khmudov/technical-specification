"""Реестр инструментов ассистента.

Инструмент — функция сервисного слоя, которую модель может вызвать. Модель видит
только имя, описание и JSON Schema аргументов; SQL она не пишет. Аргументы от модели
валидируются Pydantic-схемой как любой внешний ввод, инструмент работает с правами
текущего пользователя. Сами инструменты — в tool_set.py.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.llm.types import ToolCall, ToolSpec
from app.models import User

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    session: AsyncSession
    user: User
    conversation_id: int
    message_id: int
    # Code Interpreter: файлы хода у провайдера (None — недоступен, например без ключа)
    ci: Any = None
    # Текст сообщения пользователя в этом ходе (настоящий, без псевдонимов) — для проверки,
    # что запоминается сказанное пользователем, а не текст из данных
    user_text: str = ""
    # Сервис курсов (None — настоящий из настроек; в тестах подменяется)
    rates: Any = None
    # Результаты инструментов этого хода: факт для памяти не может быть цитатой из них
    data_seen: list[str] = field(default_factory=list)


@dataclass
class ToolOutcome:
    output: dict[str, Any]  # уходит модели (JSON)
    summary: str  # коротко для пользователя под ответом
    ok: bool = True
    extra: dict[str, Any] = field(default_factory=dict)  # для интерфейса (например, действие)


Handler = Callable[[ToolContext, Any], Awaitable[ToolOutcome]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Handler

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.args.model_json_schema())


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools = {t.name: t for t in tools or []}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> tuple[ToolOutcome, Any]:
        """Выполнить вызов модели. Никогда не бросает: любая ошибка — результат для модели.

        Возвращает итог и разобранные аргументы (для журнала под ответом).
        """
        tool = self._tools.get(call.name)
        if tool is None:
            return _error(f"Инструмента {call.name} нет"), None
        try:
            raw = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            return _error("Аргументы не являются корректным JSON"), None
        try:
            args = tool.args.model_validate(raw)
        except ValidationError as exc:
            allowed = ", ".join(tool.args.model_fields)
            problems = "; ".join(
                f"лишний аргумент «{'.'.join(str(p) for p in e['loc'])}» — такого нет "
                f"(есть: {allowed})"
                if e["type"] == "extra_forbidden"
                else f"{'.'.join(str(p) for p in e['loc']) or 'аргументы'}: {_reason(e)}"
                for e in exc.errors()
            )
            return _error(f"Некорректные аргументы: {problems}"), raw
        try:
            outcome = await tool.handler(ctx, args)
            ctx.data_seen.append(json.dumps(outcome.output, ensure_ascii=False, default=str))
            return outcome, args.model_dump(mode="json")
        except DomainError as exc:
            # Отказ по правам, «не найдено», «не хватает» — понятный текст и для модели
            await _recover(ctx)
            return _error(exc.message), args.model_dump(mode="json")
        except Exception:
            logger.exception("tool %s failed", call.name)
            await _recover(ctx)
            return _error("Внутренняя ошибка при получении данных"), args.model_dump(mode="json")


async def _recover(ctx: ToolContext) -> None:
    """Откатить несохранённое после ошибки и вернуть сессию в рабочее состояние.

    rollback() помечает все объекты сессии устаревшими, в том числе ctx.user. Следующий
    инструмент в том же ответе читал user.role, а ленивая подгрузка в async-сессии
    невозможна — и «модель ошиблась → исправилась» заканчивалось внутренней ошибкой.
    """
    await ctx.session.rollback()
    if inspect(ctx.user).persistent:
        await ctx.session.refresh(ctx.user)


def _reason(error: dict) -> str:
    """Причина ошибки валидации по-русски (как в ответах API), без «Value error, …»."""
    from app.api.errors import _message

    if error["type"] == "value_error" and error.get("ctx", {}).get("error"):
        return str(error["ctx"]["error"])
    return _message(error)


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(output={"error": message}, summary=message, ok=False)


def default_registry() -> ToolRegistry:
    """Набор инструментов ассистента."""
    from app.assistant.tool_set import TOOLS

    return ToolRegistry(TOOLS)
