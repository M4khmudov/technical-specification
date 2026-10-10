"""Промпты лежат отдельными файлами в app/prompts/*.md, а не строками в коде.

Подстановки — через string.Template ($name), чтобы фигурные скобки в тексте
промпта (JSON-примеры) не ломали форматирование.
"""

from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache
def _read(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Промпт {name} не найден: {path}")
    return path.read_text(encoding="utf-8")


def load_prompt(name: str, **values: object) -> str:
    """Текст промпта с подставленными значениями. Неподставленная $переменная — ошибка."""
    return Template(_read(name)).substitute({k: str(v) for k, v in values.items()})
