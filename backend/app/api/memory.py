from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.deps import CurrentUser, SessionDep
from app.services import memory as service

router = APIRouter(prefix="/memory", tags=["assistant"])


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    text: str
    conversation_id: int | None
    created_at: datetime


@router.get("")
async def list_facts(session: SessionDep, user: CurrentUser) -> list[FactOut]:
    """Что ассистент помнит о текущем пользователе (только свои факты)."""
    return [FactOut.model_validate(f) for f in await service.list_facts(session, user)]


@router.delete("/{fact_id}", status_code=204)
async def forget(fact_id: int, session: SessionDep, user: CurrentUser) -> None:
    await service.forget(session, user, fact_id)
