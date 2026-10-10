"""Права ролей. Проверяются в сервисном слое: кнопка интерфейса и инструмент
ассистента вызывают один и тот же сервис, поэтому через ассистента нельзя сделать
то, что нельзя сделать кнопкой."""

from enum import StrEnum

from app.core.errors import PermissionDeniedError
from app.models import User
from app.models.enums import ROLE_LABELS, Role


class Permission(StrEnum):
    RECEIPT_CREATE = "receipt.create"
    BATCH_REVIEW = "batch.review"  # приёмка / брак
    JOB_MANAGE = "job.manage"  # создать, запланировать, дозарезервировать, отменить
    JOB_RUN = "job.run"  # запустить, завершить
    AUDIT_VIEW = "audit.view"  # общий журнал изменений
    MARKING_RETRY = "marking.retry"  # повторить отправку в сервис маркировки вручную


# Что именно запрещено — для текста ошибки
ACTION_TEXT = {
    Permission.RECEIPT_CREATE: "оформлять поступления",
    Permission.BATCH_REVIEW: "принимать партии и отправлять их в брак",
    Permission.JOB_MANAGE: "создавать, планировать и отменять задания",
    Permission.JOB_RUN: "запускать и завершать задания",
    Permission.AUDIT_VIEW: "просматривать общий журнал изменений",
    Permission.MARKING_RETRY: "повторять отправку отчёта в сервис маркировки",
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.STOREKEEPER: frozenset({Permission.RECEIPT_CREATE, Permission.BATCH_REVIEW}),
    Role.TECHNOLOGIST: frozenset({Permission.JOB_MANAGE, Permission.JOB_RUN}),
    Role.MANAGER: frozenset(Permission),
}


def has_permission(user: User, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[user.role]


def require(user: User, permission: Permission) -> None:
    if has_permission(user, permission):
        return
    allowed = [ROLE_LABELS[r] for r, perms in ROLE_PERMISSIONS.items() if permission in perms]
    raise PermissionDeniedError(
        f"Недостаточно прав: роль «{ROLE_LABELS[user.role]}» не может "
        f"{ACTION_TEXT[permission]}. Это может {' или '.join(allowed)}."
    )
