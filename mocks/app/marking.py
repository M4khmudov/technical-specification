"""Мок сервиса маркировки.

Ведёт себя как ненадёжный внешний сервис:
- ~30% запросов отвечают 500 (MARKING_FAILURE_RATE);
- ~15% запросов отвечают через 10 секунд (MARKING_SLOW_RATE, MARKING_SLOW_SECONDS).

Идемпотентность: заголовок Idempotency-Key обязателен. Отчёт сохраняется ДО
возможной задержки, поэтому клиент, не дождавшийся медленного ответа, при повторе с
тем же ключом получит уже существующий отчёт (duplicate=true), а не создаст второй.
Состояние хранится в памяти процесса — для мока этого достаточно.
"""

import asyncio
import os
import random
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/marking", tags=["marking"])

FAILURE_RATE = float(os.environ.get("MARKING_FAILURE_RATE", "0.3"))
SLOW_RATE = float(os.environ.get("MARKING_SLOW_RATE", "0.15"))
SLOW_SECONDS = float(os.environ.get("MARKING_SLOW_SECONDS", "10"))

_reports: dict[str, dict[str, Any]] = {}  # Idempotency-Key → принятый отчёт
_random = random.Random()  # noqa: S311 — для имитации сбоев криптостойкость не нужна


@router.post("/reports")
async def accept_report(
    payload: dict[str, Any],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Не передан заголовок Idempotency-Key")

    if _random.random() < FAILURE_RATE:
        raise HTTPException(500, "Сервис маркировки временно недоступен")

    existing = _reports.get(idempotency_key)
    if existing is None:
        existing = {
            "report_id": uuid.uuid4().hex,
            "received_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        _reports[idempotency_key] = existing
        duplicate = False
    else:
        duplicate = True

    if _random.random() < SLOW_RATE:
        await asyncio.sleep(SLOW_SECONDS)

    return {"report_id": existing["report_id"], "duplicate": duplicate}


@router.get("/reports")
async def list_reports() -> dict[str, Any]:
    """Для проверки вручную: сколько уникальных отчётов принято."""
    return {"count": len(_reports), "keys": list(_reports)}
