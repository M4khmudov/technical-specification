"""Ограничение попыток входа: подбор пароля к учётной записи.

После MAX_FAILURES неудачных попыток за WINDOW по одному логину вход по нему
закрывается до конца окна. Удачный вход сбрасывает счётчик. Состояние — в памяти
процесса (бэкенд запускается одним процессом).
"""

import time
from collections import defaultdict, deque

from app.core.errors import TooManyRequestsError

MAX_FAILURES = 5
WINDOW_SECONDS = 300


class LoginThrottle:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = WINDOW_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, login: str) -> deque[float]:
        attempts = self._failures[login.strip().lower()]
        border = time.monotonic() - self.window
        while attempts and attempts[0] < border:
            attempts.popleft()
        return attempts

    def check(self, login: str) -> None:
        attempts = self._recent(login)
        if len(attempts) >= self.max_failures:
            wait = int(attempts[0] + self.window - time.monotonic()) // 60 + 1
            raise TooManyRequestsError(
                f"Слишком много неудачных попыток входа. Попробуйте через {wait} мин."
            )

    def failed(self, login: str) -> None:
        self._recent(login).append(time.monotonic())

    def succeeded(self, login: str) -> None:
        self._failures.pop(login.strip().lower(), None)


login_throttle = LoginThrottle()
