"""Отправка отчётов о выпуске в сервис маркировки.

Схема (transactional outbox):
1. complete_job в той же транзакции создаёт MarkingReport(status=pending) с ключом
   идемпотентности. Завершение задания не ходит в сеть и не ждёт сервис.
2. После коммита задача ставится в очередь arq (быстрый путь). Если Redis недоступен —
   не страшно: периодическая задача воркера подбирает отчёты, у которых наступило
   next_attempt_at.
3. Отправка берёт строку отчёта FOR UPDATE SKIP LOCKED — один отчёт не отправляется
   параллельно двумя воркерами. Каждая попытка пишется в marking_attempts.
4. 5xx, таймаут, сетевая ошибка, 408/429 → повтор с растущей паузой (экспонента +
   джиттер). Прочие 4xx — ошибка самого запроса, повтор не поможет → failed.
   После marking_max_attempts попыток → failed, руководитель может повторить вручную.
5. Ключ идемпотентности не меняется между повторами: если сервис обработал запрос,
   но мы не дождались ответа, повтор вернёт тот же отчёт, а не создаст дубль.
"""

import logging
import random
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import Permission, require
from app.models import MarkingAttempt, MarkingReport, ProductionJob, User
from app.models.enums import JobStatus, MarkingStatus
from app.services import audit

logger = logging.getLogger(__name__)

RETRYABLE_4XX = {408, 425, 429}
_random = random.Random()  # noqa: S311 — джиттер, не криптография


def _now() -> datetime:
    return datetime.now(UTC)


def backoff_delay(attempt: int) -> timedelta:
    """Пауза перед попыткой attempt+1: base·2^(attempt-1), не больше max, джиттер ±20%."""
    settings = get_settings()
    raw = min(
        settings.marking_backoff_base_seconds * 2 ** (attempt - 1),
        settings.marking_backoff_max_seconds,
    )
    return timedelta(seconds=raw * _random.uniform(0.8, 1.2))


def build_report(job: ProductionJob) -> MarkingReport:
    """Отчёт для сервиса маркировки. Персональных данных в нём нет."""
    payload: dict[str, Any] = {
        "job_number": job.number,
        "product": job.recipe.name,
        "quantity": job.quantity,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "materials": [
            {"code": jm.material.code, "actual_qty": str(jm.actual_qty)} for jm in job.materials
        ],
    }
    return MarkingReport(
        job_id=job.id,
        idempotency_key=uuid.uuid4().hex,
        payload=payload,
        status=MarkingStatus.PENDING,
        next_attempt_at=_now(),
    )


# ---------------------------------------------------------------- очередь


async def enqueue(report_id: int) -> None:
    """Быстрый путь: поставить отправку в очередь. Ошибки не роняют вызывающего."""
    if not get_settings().marking_enqueue_enabled:
        return
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        try:
            await pool.enqueue_job("send_marking_report", report_id)
        finally:
            await pool.aclose()
    except Exception:  # noqa: BLE001 — отчёт подберёт периодическая задача
        logger.warning("marking report %s not enqueued, will be picked up by cron", report_id)


# ---------------------------------------------------------------- отправка


class MarkingClient(Protocol):
    async def post(self, url: str, *, json: Any, headers: dict[str, str]) -> httpx.Response: ...


def default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(get_settings().marking_timeout_seconds))


async def send_report(session: AsyncSession, report_id: int, client: MarkingClient) -> str:
    """Одна попытка отправки. Возвращает итоговый статус отчёта (для логов и тестов)."""
    settings = get_settings()
    report = await session.scalar(
        select(MarkingReport)
        .where(MarkingReport.id == report_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if report is None:
        await session.rollback()
        return "locked"  # уже отправляет другой воркер (или отчёта нет)
    if report.status != MarkingStatus.PENDING:
        status = report.status.value  # после rollback объект истекает — читаем до
        await session.rollback()
        return status

    attempt_no = report.attempts_count + 1
    started = _now()
    clock = time.monotonic()
    http_status: int | None = None
    error: str | None = None
    try:
        response = await client.post(
            f"{settings.marking_service_url.rstrip('/')}/reports",
            json=report.payload,
            headers={"Idempotency-Key": report.idempotency_key},
        )
        http_status = response.status_code
        if response.is_success:
            outcome = "sent"
        else:
            error = response.text[:500]
            retryable = response.status_code >= 500 or response.status_code in RETRYABLE_4XX
            outcome = "retry" if retryable else "failed"
    except httpx.TimeoutException:
        outcome, error = "retry", f"Нет ответа за {settings.marking_timeout_seconds:g} с"
    except httpx.HTTPError as exc:
        outcome, error = "retry", f"Сетевая ошибка: {exc.__class__.__name__}"

    session.add(
        MarkingAttempt(
            report_id=report.id,
            attempt_no=attempt_no,
            started_at=started,
            duration_ms=int((time.monotonic() - clock) * 1000),
            http_status=http_status,
            error=error,
        )
    )
    report.attempts_count = attempt_no

    if outcome == "retry" and attempt_no >= settings.marking_max_attempts:
        outcome = "failed"
    if outcome == "sent":
        report.status = MarkingStatus.SENT
        report.sent_at = _now()
        report.next_attempt_at = None
        job = await session.get(ProductionJob, report.job_id, with_for_update=True)
        if job is not None and job.status == JobStatus.COMPLETED:
            job.status = JobStatus.MARKED
            audit.record_system(
                session,
                "job",
                job.id,
                "marked",
                before={"status": JobStatus.COMPLETED.value},
                after={"status": JobStatus.MARKED.value, "attempts": attempt_no},
            )
    elif outcome == "retry":
        report.next_attempt_at = _now() + backoff_delay(attempt_no)
    else:
        report.status = MarkingStatus.FAILED
        report.next_attempt_at = None
    await session.commit()
    logger.info("marking report %s attempt %s: %s", report.id, attempt_no, outcome)
    return outcome


async def due_report_ids(session: AsyncSession, limit: int = 50) -> list[int]:
    rows = await session.scalars(
        select(MarkingReport.id)
        .where(
            MarkingReport.status == MarkingStatus.PENDING,
            MarkingReport.next_attempt_at <= _now(),
        )
        .order_by(MarkingReport.next_attempt_at)
        .limit(limit)
    )
    return list(rows.all())


# ---------------------------------------------------------------- чтение и ручной повтор


async def get_report(session: AsyncSession, job_id: int) -> MarkingReport | None:
    return await session.scalar(
        select(MarkingReport)
        .where(MarkingReport.job_id == job_id)
        .execution_options(populate_existing=True)
    )


async def list_attempts(session: AsyncSession, report_id: int) -> list[MarkingAttempt]:
    rows = await session.scalars(
        select(MarkingAttempt)
        .where(MarkingAttempt.report_id == report_id)
        .order_by(MarkingAttempt.attempt_no)
    )
    return list(rows.all())


async def retry_failed(session: AsyncSession, actor: User, job_id: int) -> MarkingReport:
    """Повторить отправку после исчерпания попыток. Ключ идемпотентности тот же."""
    require(actor, Permission.MARKING_RETRY)
    report = await session.scalar(
        select(MarkingReport).where(MarkingReport.job_id == job_id).with_for_update()
    )
    if report is None:
        raise NotFoundError(f"Отчёт в сервис маркировки по заданию #{job_id} не найден")
    if report.status != MarkingStatus.FAILED:
        raise ConflictError("Повторить можно только отчёт, отправка которого не удалась")
    report.status = MarkingStatus.PENDING
    report.next_attempt_at = _now()
    audit.record(session, actor, "marking_report", report.id, "retry")
    await session.commit()
    return report
