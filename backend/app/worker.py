"""Фоновый воркер arq: отправка отчётов в сервис маркировки.

send_marking_report — одна попытка; при неудаче следующая ставится в очередь с
паузой (backoff). resend_due_marking_reports раз в 30 секунд подбирает отчёты,
у которых наступило время попытки, — страховка на случай, если задача в очереди
потерялась (Redis перезапущен, постановка в очередь не удалась и т.п.).
"""

import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import MarkingReport
from app.models.enums import MarkingStatus
from app.services import marking

logger = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    ctx["marking_client"] = marking.default_client()


async def shutdown(ctx: dict) -> None:
    await ctx["marking_client"].aclose()


async def send_marking_report(ctx: dict, report_id: int) -> str:
    async with SessionLocal() as session:
        outcome = await marking.send_report(session, report_id, ctx["marking_client"])
        if outcome == "retry":
            report = await session.get(MarkingReport, report_id)
            if report and report.status == MarkingStatus.PENDING and report.next_attempt_at:
                await ctx["redis"].enqueue_job(
                    "send_marking_report",
                    report_id,
                    _defer_until=report.next_attempt_at,
                    # один отложенный повтор на попытку — без дублей в очереди
                    _job_id=f"marking:{report_id}:{report.attempts_count}",
                )
        return outcome


async def resend_due_marking_reports(ctx: dict) -> int:
    async with SessionLocal() as session:
        ids = await marking.due_report_ids(session)
    for report_id in ids:
        async with SessionLocal() as session:
            await marking.send_report(session, report_id, ctx["marking_client"])
    return len(ids)


class WorkerSettings:
    functions = [send_marking_report]
    cron_jobs = [cron(resend_due_marking_reports, second={0, 30}, run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
