from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.models import Recipe, RecipeItem
from app.models.enums import JobStatus
from app.schemas.production import JobCompleteIn, JobCreateIn, JobListItem, JobOut
from app.schemas.stock import Page
from app.services import production as service

router = APIRouter(tags=["jobs"])


class RecipeItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    material_id: int
    qty_per_unit: Decimal


class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    items: list[RecipeItemOut]


@router.get("/recipes")
async def list_recipes(session: SessionDep, _: CurrentUser) -> list[RecipeOut]:
    rows = await session.scalars(
        select(Recipe).options(selectinload(Recipe.items).selectinload(RecipeItem.material))
    )
    return [RecipeOut.model_validate(r) for r in rows]


@router.post("/jobs", status_code=201)
async def create_job(
    data: JobCreateIn,
    session: SessionDep,
    user: CurrentUser,
    plan: Annotated[bool, Query(description="Сразу запланировать и зарезервировать")] = False,
) -> JobOut:
    return await service.create_job(session, user, data, plan=plan)


@router.get("/jobs")
async def list_jobs(
    session: SessionDep,
    _: CurrentUser,
    status: JobStatus | None = None,
    planned_from: date | None = None,
    planned_to: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[JobListItem]:
    return await service.list_jobs(session, status, planned_from, planned_to, page, size)


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, session: SessionDep, _: CurrentUser) -> JobOut:
    return await service.get_job(session, job_id)


@router.post("/jobs/{job_id}/plan")
async def plan_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    return await service.plan_job(session, user, job_id)


@router.post("/jobs/{job_id}/reserve")
async def reserve_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    return await service.reserve_job(session, user, job_id)


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    return await service.start_job(session, user, job_id)


@router.post("/jobs/{job_id}/complete")
async def complete_job(
    job_id: int, data: JobCompleteIn, session: SessionDep, user: CurrentUser
) -> JobOut:
    return await service.complete_job(session, user, job_id, data)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobOut:
    return await service.cancel_job(session, user, job_id)
