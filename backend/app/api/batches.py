from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, Rates, SessionDep
from app.models.enums import BatchStatus, Currency
from app.schemas.stock import (
    BatchDetailOut,
    BatchFilter,
    BatchOut,
    BatchSort,
    Page,
    ReceiptIn,
    ReceiptOut,
)
from app.services import batches as service

router = APIRouter(tags=["batches"])


@router.post("/receipts", status_code=201)
async def create_receipt(
    data: ReceiptIn, session: SessionDep, user: CurrentUser, rates: Rates
) -> ReceiptOut:
    return await service.create_receipt(session, user, data, rates)


@router.get("/batches")
async def list_batches(
    session: SessionDep,
    _: CurrentUser,
    number: int | None = None,
    material: Annotated[str | None, Query(max_length=100)] = None,
    material_id: int | None = None,
    supplier: Annotated[str | None, Query(max_length=100)] = None,
    status: BatchStatus | None = None,
    currency: Currency | None = None,
    received_from: date | None = None,
    received_to: date | None = None,
    sort: BatchSort = BatchSort.NUMBER,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[BatchOut]:
    filters = BatchFilter(
        number=number,
        material=material,
        material_id=material_id,
        supplier=supplier,
        status=status,
        currency=currency,
        received_from=received_from,
        received_to=received_to,
    )
    return await service.list_batches(session, filters, sort, order == "desc", page, size)


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: int, session: SessionDep, _: CurrentUser) -> BatchDetailOut:
    return await service.get_batch_history(session, batch_id)


@router.post("/batches/{batch_id}/accept")
async def accept_batch(batch_id: int, session: SessionDep, user: CurrentUser) -> BatchOut:
    return await service.accept_batch(session, user, batch_id)


class RejectIn(BaseModel):
    reason: Annotated[str, Field(max_length=500)] | None = None


@router.post("/batches/{batch_id}/reject")
async def reject_batch(
    batch_id: int, data: RejectIn, session: SessionDep, user: CurrentUser
) -> BatchOut:
    return await service.reject_batch(session, user, batch_id, data.reason)
