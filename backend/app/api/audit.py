from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep
from app.schemas.stock import AuditOut, Page
from app.services import audit as service

router = APIRouter(tags=["audit"])


class AuditEntryOut(AuditOut):
    id: int
    entity_type: str
    entity_id: int
    user_id: int | None


@router.get("/audit")
async def list_audit(
    session: SessionDep,
    user: CurrentUser,
    entity_type: Annotated[str | None, Query(max_length=64)] = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AuditEntryOut]:
    entries, total = await service.list_entries(
        session,
        user,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        size=size,
    )
    return Page[AuditEntryOut](
        items=[AuditEntryOut.model_validate(e) for e in entries], total=total, page=page, size=size
    )


@router.get("/jobs/{job_id}/history")
async def job_history(job_id: int, session: SessionDep, _: CurrentUser) -> list[AuditOut]:
    return [
        AuditOut.model_validate(e) for e in await service.entity_history(session, "job", job_id)
    ]
