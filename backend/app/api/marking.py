from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import NotFoundError
from app.models.enums import MarkingStatus
from app.services import marking as service

router = APIRouter(tags=["marking"])

STATUS_LABELS = {
    MarkingStatus.PENDING: "Отправляется",
    MarkingStatus.SENT: "Принят сервисом маркировки",
    MarkingStatus.FAILED: "Не отправлен",
}


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    attempt_no: int
    started_at: datetime
    duration_ms: int | None
    http_status: int | None
    error: str | None


class MarkingOut(BaseModel):
    status: MarkingStatus
    status_label: str
    idempotency_key: str
    attempts_count: int
    next_attempt_at: datetime | None
    sent_at: datetime | None
    attempts: list[AttemptOut]


async def _out(session, report) -> MarkingOut:
    attempts = await service.list_attempts(session, report.id)
    return MarkingOut(
        status=report.status,
        status_label=STATUS_LABELS[report.status],
        idempotency_key=report.idempotency_key,
        attempts_count=report.attempts_count,
        next_attempt_at=report.next_attempt_at,
        sent_at=report.sent_at,
        attempts=[AttemptOut.model_validate(a) for a in attempts],
    )


@router.get("/jobs/{job_id}/marking")
async def get_marking(job_id: int, session: SessionDep, _: CurrentUser) -> MarkingOut:
    report = await service.get_report(session, job_id)
    if report is None:
        raise NotFoundError("Отчёт в сервис маркировки создаётся при завершении задания")
    return await _out(session, report)


@router.post("/jobs/{job_id}/marking/retry")
async def retry_marking(job_id: int, session: SessionDep, user: CurrentUser) -> MarkingOut:
    report = await service.retry_failed(session, user, job_id)
    await service.enqueue(report.id)
    return await _out(session, report)
