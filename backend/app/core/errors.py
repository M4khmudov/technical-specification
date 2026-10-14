"""Ошибки предметной области. Сообщение — готовый текст для пользователя
(«Не хватает 2,5 кг воска»), а не 500 Internal Server Error.

Сервисный слой бросает эти исключения; HTTP-слой превращает их в ответ
{"detail": "..."} с нужным кодом, ассистент — в текст результата инструмента.
"""


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class PermissionDeniedError(DomainError):
    status_code = 403


class ConflictError(DomainError):
    """Действие недопустимо в текущем состоянии (например, партия уже принята)."""

    status_code = 409


class ExternalServiceError(DomainError):
    status_code = 503


class TooManyRequestsError(DomainError):
    status_code = 429
