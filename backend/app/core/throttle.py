"""Ограничение попыток входа: подбор пароля к учётной записи.

После MAX_FAILURES неудачных попыток за WINDOW с одного адреса по одному логину вход
с этого адреса закрывается до конца окна. Считается пара «логин + адрес»: иначе любой,
зная логин, пятью неверными паролями закрывал бы вход настоящему сотруднику. Удачный
вход сбрасывает счётчик. Состояние — в памяти процесса (бэкенд — один процесс).
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

    @staticmethod
    def _key(login: str, client: str) -> str:
        return f"{login.strip().lower()}|{client}"

    def _recent(self, login: str, client: str = "") -> deque[float]:
        attempts = self._failures[self._key(login, client)]
        border = time.monotonic() - self.window
        while attempts and attempts[0] < border:
            attempts.popleft()
        return attempts

    def check(self, login: str, client: str = "") -> None:
        attempts = self._recent(login, client)
        if len(attempts) >= self.max_failures:
            wait = int(attempts[0] + self.window - time.monotonic()) // 60 + 1
            raise TooManyRequestsError(
                f"Слишком много неудачных попыток входа. Попробуйте через {wait} мин."
            )

    def failed(self, login: str, client: str = "") -> None:
        self._recent(login, client).append(time.monotonic())

    def succeeded(self, login: str, client: str = "") -> None:
        self._failures.pop(self._key(login, client), None)


login_throttle = LoginThrottle()
