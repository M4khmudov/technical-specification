"""Перечисления предметной области. Значения хранятся в БД строками (не native enum),
чтобы добавление значения не требовало ALTER TYPE; допустимость проверяет CHECK."""

from enum import StrEnum


class Role(StrEnum):
    STOREKEEPER = "storekeeper"  # Кладовщик
    TECHNOLOGIST = "technologist"  # Технолог
    MANAGER = "manager"  # Руководитель


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class BatchStatus(StrEnum):
    PENDING = "pending"  # На проверке
    ACCEPTED = "accepted"  # Принята
    REJECTED = "rejected"  # Брак


class JobStatus(StrEnum):
    DRAFT = "draft"  # Черновик
    PLANNED = "planned"  # Запланировано
    IN_PROGRESS = "in_progress"  # В работе
    COMPLETED = "completed"  # Завершено
    MARKED = "marked"  # Сервис маркировки: отчёт принят внешним сервисом
    CANCELLED = "cancelled"  # Отменено (только из «Черновик» и «Запланировано»)


class MovementKind(StrEnum):
    """Движения по партии — из них собирается «история партии»."""

    RECEIPT = "receipt"  # поступление
    RESERVE = "reserve"  # резерв под задание
    RELEASE = "release"  # снятие резерва (отмена задания, экономия)
    CONSUME = "consume"  # списание в производство


class MarkingStatus(StrEnum):
    PENDING = "pending"  # ждёт отправки / повтора
    SENT = "sent"  # сервис подтвердил приём
    FAILED = "failed"  # попытки исчерпаны


ROLE_LABELS = {
    Role.STOREKEEPER: "Кладовщик",
    Role.TECHNOLOGIST: "Технолог",
    Role.MANAGER: "Руководитель",
}

BATCH_STATUS_LABELS = {
    BatchStatus.PENDING: "На проверке",
    BatchStatus.ACCEPTED: "Принята",
    BatchStatus.REJECTED: "Брак",
}

JOB_STATUS_LABELS = {
    JobStatus.DRAFT: "Черновик",
    JobStatus.PLANNED: "Запланировано",
    JobStatus.IN_PROGRESS: "В работе",
    JobStatus.COMPLETED: "Завершено",
    JobStatus.MARKED: "Сервис маркировки",
    JobStatus.CANCELLED: "Отменено",
}
