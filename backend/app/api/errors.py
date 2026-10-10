"""Ошибки HTTP-слоя — всегда понятным текстом в {"detail": "..."}, без трассировок."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError

logger = logging.getLogger(__name__)

# Подписи полей для сообщений; неизвестное поле показывается как есть
FIELD_LABELS = {
    "quantity": "Количество",
    "price": "Цена",
    "currency": "Валюта",
    "material_id": "Материал",
    "supplier_id": "Поставщик",
    "recipe_id": "Рецепт",
    "received_on": "Дата поступления",
    "planned_on": "Дата задания",
    "actual_qty": "Фактический расход",
    "lines": "Строки поступления",
    "login": "Логин",
    "password": "Пароль",
    "comment": "Комментарий",
    "reason": "Причина",
}


def _limit(ctx: dict, key: str) -> str:
    return str(ctx.get(key, "")).replace(".", ",")


def _message(error: dict) -> str:
    ctx = error.get("ctx") or {}
    kind = error["type"]
    messages = {
        "missing": "обязательное поле",
        "greater_than": f"должно быть больше {_limit(ctx, 'gt')}",
        "greater_than_equal": f"должно быть не меньше {_limit(ctx, 'ge')}",
        "less_than": f"должно быть меньше {_limit(ctx, 'lt')}",
        "less_than_equal": f"должно быть не больше {_limit(ctx, 'le')}",
        "int_parsing": "должно быть целым числом",
        "int_from_float": "должно быть целым числом",
        "decimal_parsing": "должно быть числом",
        "decimal_max_places": f"не больше {ctx.get('decimal_places')} знаков после запятой",
        "decimal_max_digits": "слишком большое число",
        "date_parsing": "некорректная дата, нужен формат ГГГГ-ММ-ДД",
        "date_from_datetime_parsing": "некорректная дата, нужен формат ГГГГ-ММ-ДД",
        "enum": f"допустимые значения: {ctx.get('expected', '')}",
        "string_too_long": f"не длиннее {ctx.get('max_length')} символов",
        "too_short": "список не может быть пустым",
        "too_long": f"не больше {ctx.get('max_length')} элементов",
        "string_pattern_mismatch": "некорректное значение",
        "json_invalid": "некорректный JSON",
    }
    return messages.get(kind, "некорректное значение")


def _field(loc: tuple) -> str:
    # loc вида ("body", "lines", 0, "quantity") → «Строки поступления №1 → Количество»
    parts = []
    for item in loc[1:] if loc and loc[0] in ("body", "query", "path") else loc:
        if isinstance(item, int):
            parts[-1:] = [f"{parts[-1]} №{item + 1}"] if parts else [f"№{item + 1}"]
        else:
            parts.append(FIELD_LABELS.get(item, item))
    return " → ".join(parts) or "Запрос"


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [f"{_field(tuple(e['loc']))}: {_message(e)}" for e in exc.errors()]
        return JSONResponse(
            {"detail": "Проверьте данные. " + "; ".join(problems), "errors": problems},
            status_code=422,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        # Только имя ограничения и код: текст ошибки PostgreSQL содержит значения строки
        # («Failing row contains …»), а в них могут быть ФИО и названия поставщиков
        orig = exc.orig
        logger.warning(
            "integrity error: constraint=%s sqlstate=%s",
            getattr(orig, "constraint_name", None)
            or getattr(orig.__cause__, "constraint_name", None),
            getattr(orig, "sqlstate", None) or getattr(orig.__cause__, "sqlstate", None),
        )
        return JSONResponse(
            {
                "detail": "Данные не сохранены: они противоречат текущему состоянию склада. "
                "Обновите страницу и повторите действие."
            },
            status_code=409,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse(
            {"detail": "Внутренняя ошибка сервера. Попробуйте позже."}, status_code=500
        )
