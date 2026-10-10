"""Ошибки LLM-слоя.

retryable=True — сбой на стороне провайдера (429, 5xx, таймаут, сеть): есть смысл
перейти на запасную модель. retryable=False — ошибка самого запроса (прочие 4xx):
другая модель её не исправит, повторять бессмысленно.
"""


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.message = message  # технические подробности — только для логов
        self.retryable = retryable
        self.status = status


class LLMUnavailableError(Exception):
    """Ни одна модель не ответила. Текст — для пользователя."""

    def __init__(self, user_message: str, causes: list[LLMError]):
        super().__init__(user_message)
        self.user_message = user_message
        self.causes = causes


class LLMInterruptedError(Exception):
    """Ответ оборвался посередине — часть текста пользователь уже видел.

    На запасную модель в этом случае не переходим: подменять начатый ответ
    другим было бы нечестно. Вызывающий сохраняет ответ с пометкой «оборван».
    """

    def __init__(self, user_message: str, cause: LLMError):
        super().__init__(user_message)
        self.user_message = user_message
        self.cause = cause
