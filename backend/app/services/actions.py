"""Действия ассистента с подтверждением.

Ассистент не меняет данные сам: инструмент действия создаёт «предложение» с
описанием того, что будет сделано. Выполняет его кнопка «Подтвердить» — тем же
сервисом и с теми же правами, что и обычная кнопка интерфейса.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import clock
from app.core.errors import ConflictError, DomainError, NotFoundError
from app.core.permissions import Permission, require
from app.models import AssistantAction, RecipeItem, User
from app.models.enums import ActionStatus
from app.schemas.production import JobCreateIn
from app.services import analytics, production

ACTION_TTL = timedelta(minutes=30)
CREATE_JOB = "create_job"


def _now() -> datetime:
    return datetime.now(UTC)


async def propose_create_job(
    session: AsyncSession,
    user: User,
    conversation_id: int,
    message_id: int,
    recipe_name: str,
    quantity: int,
    planned_on: date,
    comment: str | None,
) -> AssistantAction:
    """Подготовить создание и планирование задания. Данные не меняются."""
    # Права проверяем сразу: нельзя даже предложить то, что нельзя сделать кнопкой
    require(user, Permission.JOB_MANAGE)
    recipe = await analytics.find_recipe(session, recipe_name)
    # Те же правила, что у формы: количество, дата не в прошлом
    data = JobCreateIn(
        recipe_id=recipe.id, quantity=quantity, planned_on=planned_on, comment=comment
    )
    if data.planned_on < clock.today():
        raise DomainError("Дата задания не может быть в прошлом")

    items = await _recipe_items(session, recipe.id)
    free = await analytics.free_stock(session, [i.material_id for i in items])
    materials = []
    for item in items:
        need = item.qty_per_unit * quantity
        available = free.get(item.material_id, Decimal("0"))
        materials.append(
            {
                "code": item.material.code,
                "name": item.material.name,
                "unit": item.material.unit,
                "required": str(need),
                "free_now": str(available),
                "expected_deficit": str(max(need - available, Decimal("0"))),
            }
        )
    preview: dict[str, Any] = {
        "title": f"Создать и запланировать задание «{recipe.name}», {quantity} шт "
        f"на {planned_on:%d.%m.%Y}",
        "recipe": recipe.name,
        "quantity": quantity,
        "planned_on": planned_on.isoformat(),
        "comment": comment,
        "materials": materials,
        "will_have_deficit": any(Decimal(m["expected_deficit"]) > 0 for m in materials),
    }
    action = AssistantAction(
        user_id=user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        kind=CREATE_JOB,
        payload=data.model_dump(mode="json"),
        preview=preview,
        status=ActionStatus.PENDING,
    )
    session.add(action)
    await session.commit()
    return action


async def _recipe_items(session: AsyncSession, recipe_id: int) -> list[RecipeItem]:
    rows = await session.scalars(
        select(RecipeItem)
        .options(selectinload(RecipeItem.material))
        .where(RecipeItem.recipe_id == recipe_id)
        .order_by(RecipeItem.id)
    )
    return list(rows.all())


async def _locked(session: AsyncSession, user: User, action_id: int) -> AssistantAction:
    action = await session.scalar(
        select(AssistantAction)
        .where(AssistantAction.id == action_id, AssistantAction.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if action is None:  # чужое предложение неотличимо от несуществующего
        raise NotFoundError("Действие не найдено")
    if action.status == ActionStatus.PENDING and _now() - action.created_at > ACTION_TTL:
        action.status = ActionStatus.EXPIRED
        action.decided_at = _now()
        await session.commit()
        raise ConflictError("Предложение устарело — попросите ассистента подготовить его заново")
    if action.status != ActionStatus.PENDING:
        raise ConflictError("Это действие уже обработано")
    return action


async def confirm(session: AsyncSession, user: User, action_id: int) -> AssistantAction:
    """Пользователь нажал «Подтвердить»: выполняем тем же сервисом, что и кнопка.

    Предложение сначала «занимается» (статус меняется под блокировкой строки и сразу
    коммитится), и только потом выполняется. Сервис задания коммитит сам, поэтому
    держать блокировку предложения до конца нельзя: параллельный «Подтвердить» или
    «Отменить» дождался бы её снятия, увидел бы «ждёт подтверждения» и выполнил
    действие второй раз. Теперь он увидит, что действие уже обработано, — 409."""
    action = await _locked(session, user, action_id)
    if action.kind != CREATE_JOB:
        raise DomainError("Неизвестный тип действия")
    data = JobCreateIn.model_validate(action.payload)
    action_id = action.id
    action.status = ActionStatus.CONFIRMED
    action.decided_at = _now()
    await session.commit()
    try:
        job = await production.create_job(session, user, data, plan=True)
    except DomainError as exc:
        await session.rollback()
        failed = await session.get(AssistantAction, action_id, with_for_update=True)
        failed.status = ActionStatus.FAILED
        failed.error = exc.message
        await session.commit()
        raise
    done = await session.get(AssistantAction, action_id, with_for_update=True)
    done.result = {
        "job_id": job.id,
        "job_number": job.number,
        "has_deficit": job.has_deficit,
        "status_label": job.status_label,
    }
    await session.commit()
    return done


async def reject(session: AsyncSession, user: User, action_id: int) -> AssistantAction:
    action = await _locked(session, user, action_id)
    action.status = ActionStatus.REJECTED
    action.decided_at = _now()
    await session.commit()
    return action


async def for_conversation(session: AsyncSession, conversation_id: int) -> list[AssistantAction]:
    rows = await session.scalars(
        select(AssistantAction)
        .where(AssistantAction.conversation_id == conversation_id)
        .order_by(AssistantAction.id)
    )
    return list(rows.all())
