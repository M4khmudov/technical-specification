"""Журнал изменений: кто (ФИО), когда, что было, что стало."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission, require
from app.models import AuditLog, User


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)  # строкой, чтобы не терять точность
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def snapshot(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {f: _jsonable(getattr(obj, f)) for f in fields}


def record(
    session: AsyncSession,
    actor: User,
    entity_type: str,
    entity_id: int,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=actor.id,
            user_full_name=actor.full_name,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
        )
    )


SYSTEM_ACTOR = "Система (сервис маркировки)"


def record_system(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Изменение, сделанное фоновым процессом, а не пользователем."""
    session.add(
        AuditLog(
            user_id=None,
            user_full_name=SYSTEM_ACTOR,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
        )
    )


async def entity_history(session: AsyncSession, entity_type: str, entity_id: int) -> list[AuditLog]:
    """История одной сущности (партии, задания) — видна всем ролям."""
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.id)
    )
    return list(rows.all())


async def list_entries(
    session: AsyncSession,
    actor: User,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[AuditLog], int]:
    """Общий журнал изменений — только для тех, у кого есть право audit.view."""
    require(actor, Permission.AUDIT_VIEW)
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= datetime.combine(date_from, time.min, UTC))
    if date_to is not None:
        stmt = stmt.where(
            AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), time.min, UTC)
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = await session.scalars(
        stmt.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size)
    )
    return list(rows.all()), total
