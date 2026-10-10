"""Минимальные фабрики тестовых данных."""

from datetime import date
from decimal import Decimal
from itertools import count

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, Material, Receipt, Recipe, RecipeItem, Supplier, User
from app.models.enums import BatchStatus, Currency, Role

_seq = count(1)


async def make_user(s: AsyncSession, role: Role = Role.MANAGER, **kw) -> User:
    n = next(_seq)
    user = User(
        login=kw.pop("login", f"user{n}"),
        full_name=kw.pop("full_name", f"Тестов Тест {n}"),
        password_hash="x",
        role=role,
        **kw,
    )
    s.add(user)
    await s.flush()
    return user


async def make_material(s: AsyncSession, **kw) -> Material:
    n = next(_seq)
    material = Material(
        code=kw.pop("code", f"M{n:03}"), name=kw.pop("name", f"Материал {n}"), unit="кг", **kw
    )
    s.add(material)
    await s.flush()
    return material


async def make_supplier(s: AsyncSession, **kw) -> Supplier:
    supplier = Supplier(name=kw.pop("name", f"Поставщик {next(_seq)}"), **kw)
    s.add(supplier)
    await s.flush()
    return supplier


async def make_batch(
    s: AsyncSession,
    material: Material,
    qty: str | Decimal = "10",
    *,
    user: User | None = None,
    received_on: date = date(2026, 10, 1),
    status: BatchStatus = BatchStatus.ACCEPTED,
    price: str = "100",
) -> Batch:
    user = user or await make_user(s)
    receipt = Receipt(
        supplier_id=(await make_supplier(s)).id, received_on=received_on, created_by_id=user.id
    )
    s.add(receipt)
    await s.flush()
    batch = Batch(
        receipt_id=receipt.id,
        material_id=material.id,
        received_on=received_on,
        quantity=Decimal(qty),
        remaining_qty=Decimal(qty),
        price=Decimal(price),
        currency=Currency.RUB,
        exchange_rate=Decimal("1"),
        unit_cost_rub=Decimal(price),
        status=status,
    )
    s.add(batch)
    await s.flush()
    return batch


async def make_recipe(s: AsyncSession, norms: dict[Material, str], name: str | None = None):
    recipe = Recipe(name=name or f"Рецепт {next(_seq)}")
    recipe.items = [RecipeItem(material_id=m.id, qty_per_unit=Decimal(q)) for m, q in norms.items()]
    s.add(recipe)
    await s.flush()
    return recipe
