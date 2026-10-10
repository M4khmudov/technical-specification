"""Справочники для форм интерфейса."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import Material, Supplier

router = APIRouter(tags=["catalog"])


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    unit: str


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


@router.get("/materials")
async def list_materials(session: SessionDep, _: CurrentUser) -> list[MaterialOut]:
    rows = await session.scalars(select(Material).order_by(Material.code))
    return [MaterialOut.model_validate(m) for m in rows]


@router.get("/suppliers")
async def list_suppliers(session: SessionDep, _: CurrentUser) -> list[SupplierOut]:
    rows = await session.scalars(select(Supplier).order_by(Supplier.name))
    return [SupplierOut.model_validate(s) for s in rows]
