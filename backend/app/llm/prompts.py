"""Промпты лежат отдельными файлами в app/prompts/*.md, а не строками в коде.

Подстановки — через string.Template ($name), чтобы фигурные скобки в тексте
промпта (JSON-примеры) не ломали форматирование.
"""

import re
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


@lru_cache
def _tool_descriptions() -> dict[str, str]:
    """Разделы «## имя» файла tools.md → описание инструмента."""
    sections = re.split(r"^## (\w+)\s*$", _read("tools"), flags=re.M)
    return {name: text.strip() for name, text in zip(sections[1::2], sections[2::2], strict=True)}


def tool_description(name: str) -> str:
    """Описание инструмента для модели — из app/prompts/tools.md, а не строкой в коде."""
    try:
        return _tool_descriptions()[name]
    except KeyError as exc:
        raise KeyError(f"Нет описания инструмента {name} в prompts/tools.md") from exc


def load_prompt(name: str, **values: object) -> str:
    """Текст промпта с подставленными значениями. Неподставленная $переменная — ошибка."""
    return Template(_read(name)).substitute({k: str(v) for k, v in values.items()})
