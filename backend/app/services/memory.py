"""Память ассистента: факты о пользователе.

Факты читаются и меняются только по user_id текущего пользователя — чужие факты не
попадают в промпт никогда. Пользователь видит список и удаляет любой факт.
"""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.models import User, UserMemory
from app.services import audit

MAX_FACTS = 30
MAX_LENGTH = 300


def normalize(text: str) -> str:
    return re.sub(r"[\s.!,;:«»\"']+", " ", text.lower()).strip()


async def list_facts(session: AsyncSession, user: User) -> list[UserMemory]:
    rows = await session.scalars(
        select(UserMemory).where(UserMemory.user_id == user.id).order_by(UserMemory.id)
    )
    return list(rows.all())


async def remember(
    session: AsyncSession, user: User, text: str, conversation_id: int | None = None
) -> tuple[UserMemory, bool]:
    """Запомнить факт. Возвращает (факт, создан ли новый). Дубль — не ошибка."""
    text = " ".join(text.split())
    if len(text) < 3:
        raise DomainError("Факт слишком короткий")
    if len(text) > MAX_LENGTH:
        raise DomainError(f"Факт слишком длинный: не больше {MAX_LENGTH} символов")
    key = normalize(text)
    existing = await session.scalar(
        select(UserMemory).where(UserMemory.user_id == user.id, UserMemory.normalized == key)
    )
    if existing:
        return existing, False
    count = await session.scalar(
        select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user.id)
    )
    if count >= MAX_FACTS:
        raise ConflictError(
            f"В памяти уже {MAX_FACTS} фактов — удалите ненужные на странице ассистента"
        )
    fact = UserMemory(user_id=user.id, text=text, normalized=key, conversation_id=conversation_id)
    session.add(fact)
    await session.flush()
    # Память влияет на ответы ассистента — её изменения видны в журнале
    audit.record(session, user, "memory", fact.id, "remember", after={"text": text})
    await session.commit()
    return fact, True


async def forget(session: AsyncSession, user: User, fact_id: int) -> None:
    fact = await session.scalar(
        select(UserMemory).where(UserMemory.id == fact_id, UserMemory.user_id == user.id)
    )
    if fact is None:  # чужой факт неотличим от несуществующего
        raise NotFoundError("Факт не найден")
    audit.record(session, user, "memory", fact.id, "forget", before={"text": fact.text})
    await session.delete(fact)
    await session.commit()
