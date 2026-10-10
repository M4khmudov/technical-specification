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


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    STREAMING = "streaming"  # ответ ещё генерируется
    COMPLETE = "complete"
    STOPPED = "stopped"  # пользователь нажал «Остановить»
    INTERRUPTED = "interrupted"  # оборвался: сбой модели или потеря соединения
    ERROR = "error"  # модель недоступна, ответа нет


class FileOrigin(StrEnum):
    UPLOAD = "upload"  # загрузил пользователь
    GENERATED = "generated"  # создал код модели в Code Interpreter


class ActionStatus(StrEnum):
    PENDING = "pending"  # ждёт «Подтвердить»
    CONFIRMED = "confirmed"  # выполнено
    REJECTED = "rejected"  # пользователь отказался
    EXPIRED = "expired"  # не подтвердили вовремя
    FAILED = "failed"  # при подтверждении сервис вернул ошибку


ACTION_STATUS_LABELS = {
    ActionStatus.PENDING: "Ждёт подтверждения",
    ActionStatus.CONFIRMED: "Выполнено",
    ActionStatus.REJECTED: "Отменено",
    ActionStatus.EXPIRED: "Истекло",
    ActionStatus.FAILED: "Не выполнено",
}


MESSAGE_STATUS_LABELS = {
    MessageStatus.STREAMING: "Отвечает…",
    MessageStatus.COMPLETE: "Готово",
    MessageStatus.STOPPED: "Остановлено пользователем",
    MessageStatus.INTERRUPTED: "Ответ оборван",
    MessageStatus.ERROR: "Ошибка",
}

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
