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
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'аргументы'}: {e['msg']}"
                for e in exc.errors()
            )
            return _error(f"Некорректные аргументы: {problems}"), raw
        try:
            return await tool.handler(ctx, args), args.model_dump(mode="json")
        except DomainError as exc:
            # Отказ по правам, «не найдено», «не хватает» — понятный текст и для модели
            await ctx.session.rollback()
            return _error(exc.message), args.model_dump(mode="json")
        except Exception:
            logger.exception("tool %s failed", call.name)
            await ctx.session.rollback()
            return _error("Внутренняя ошибка при получении данных"), args.model_dump(mode="json")


def _error(message: str) -> ToolOutcome:
    return ToolOutcome(output={"error": message}, summary=message, ok=False)


def default_registry() -> ToolRegistry:
    """Набор инструментов ассистента."""
    from app.assistant.tool_set import TOOLS

    return ToolRegistry(TOOLS)
