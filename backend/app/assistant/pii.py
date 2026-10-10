"""Псевдонимизация персональных данных перед отправкой в LLM.

ФИО сотрудников и названия поставщиков заменяются псевдонимами «Сотрудник-N» /
«Поставщик-N», где N — id сущности. Поэтому один человек или поставщик получает один
и тот же псевдоним при любом написании и во всех диалогах, а хранить таблицу
соответствий не нужно. Обратная замена — только перед показом пользователю.

Распознаются варианты написания:
- поставщик: «ООО «Восковой двор»», «Восковой двор, ООО», «Воскового двора»,
  «ИП Смирнова О. В.» — ядро названия без организационно-правовой формы, кавычек и
  запятых, слова с падежными окончаниями;
- человек: «Петров Иван Сергеевич», «Петров Иван», «Петрову И.», «Петров И. С.»,
  «И. С. Петров», «Иван Петров», «Петрова». Окончания фамилии — из ограниченного
  набора падежных, поэтому «на Петровке» человеком не считается.
"""

import re
from dataclasses import dataclass

LEGAL_FORMS = r"(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|LLC|Ltd\.?|GmbH|Inc\.?|Co\.?)"
QUOTES = "«»\"'„“”"
EMPLOYEE = "Сотрудник"
SUPPLIER = "Поставщик"
PSEUDONYM_RE = re.compile(rf"\b({EMPLOYEE}|{SUPPLIER})-(\d+)\b", re.IGNORECASE)

# Падежные окончания (склонение фамилий на -ов/-ев/-ин и имён; оба рода)
SURNAME_ENDINGS = ["", "а", "у", "ым", "ом", "е", "ой", "ою", "ы", "ых", "ым"]
NAME_ENDINGS_HARD = ["", "а", "у", "ом", "ем", "е", "ы", "ой"]
NAME_ENDINGS_SOFT = ["я", "и", "ю", "ей", "е", "а", "ы", "у", "ой"]

B = r"(?<![\w-])"  # граница слова слева (дефис — часть псевдонима)
E = r"(?![\w-])"


def _alt(items: list[str]) -> str:
    return "(?:" + "|".join(sorted({re.escape(i) for i in items}, key=len, reverse=True)) + ")"


def _surname(word: str) -> str:
    """«Петров»/«Петрова»/«Смирнова» → корень + падежные окончания."""
    lower = word.lower()
    root = word[:-1] if lower.endswith(("ова", "ева", "ина", "ына")) else word
    if root.lower().endswith(("ов", "ев", "ин", "ын")):
        return re.escape(root) + _alt(SURNAME_ENDINGS)
    if lower.endswith(("ая", "ий", "ой", "ый")):  # Горная, Горный
        return re.escape(word[:-2]) + _alt(["ая", "ой", "ую", "ий", "ого", "ому", "им", "ом", "ый"])
    return _name(word)


def _name(word: str) -> str:
    """Имя или отчество с падежными окончаниями: Иван/Ивана/Ивану, Мария/Марии."""
    lower = word.lower()
    if lower.endswith(("а", "я")):
        return re.escape(word[:-1]) + _alt(NAME_ENDINGS_SOFT)
    if lower.endswith("й"):  # Андрей, Сергей
        return re.escape(word[:-1]) + _alt(["й", "я", "ю", "ем", "е"])
    return re.escape(word) + _alt(NAME_ENDINGS_HARD)


def _initial(word: str) -> str:
    return re.escape(word[0]) + r"\."


def person_patterns(full_name: str) -> list[str]:
    """Регулярные выражения для вариантов написания ФИО (от длинных к коротким)."""
    parts = full_name.split()
    if not parts:
        return []
    s = _surname(parts[0])
    if len(parts) == 1:
        return [s]
    f, fi = _name(parts[1]), _initial(parts[1])
    p, pi = (_name(parts[2]), _initial(parts[2])) if len(parts) > 2 else (None, None)
    ws = r"\s+"
    opt_ws = r"\s*"
    patterns = []
    if p:
        patterns += [
            f"{s}{ws}{f}{ws}{p}",  # Петров Иван Сергеевич
            f"{f}{ws}{p}{ws}{s}",  # Иван Сергеевич Петров
            f"{s}{ws}{fi}{opt_ws}{pi}",  # Петров И. С. / Петров И.С.
            f"{fi}{opt_ws}{pi}{opt_ws}{s}",  # И. С. Петров
            f"{f}{ws}{p}",  # Иван Сергеевич
        ]
    patterns += [
        f"{s}{ws}{f}",  # Петров Иван
        f"{f}{ws}{s}",  # Иван Петров
        f"{s}{ws}{fi}",  # Петрову И.
        f"{fi}{opt_ws}{s}",  # И. Петров
        s,  # Петров, Петрову
    ]
    return patterns


def _core_words(name: str) -> list[str]:
    text = re.sub(LEGAL_FORMS, " ", name)
    text = re.sub(f"[{QUOTES},.]", " ", text)
    return [w for w in text.split() if w]


def _looks_like_person(words: list[str]) -> bool:
    return len(words) in (2, 3) and all(re.fullmatch(r"[А-ЯЁ][а-яё]+", w) for w in words)


def supplier_patterns(name: str) -> list[str]:
    words = _core_words(name)
    if not words:
        return [re.escape(name)]
    if _looks_like_person(words):  # ИП Смирнова Ольга Викторовна
        persons = person_patterns(" ".join(words))
        legal = rf"(?:{LEGAL_FORMS}\s+)?"
        return [legal + p for p in persons]
    core_parts = []
    for w in words:
        if re.fullmatch(r"[а-яёА-ЯЁ]{5,}", w):  # «Воскового двора» — окончания
            core_parts.append(re.escape(w[:-2]) + r"[а-яё]{0,4}")
        elif re.fullmatch(r"[а-яёА-ЯЁ]{3,4}", w):
            core_parts.append(re.escape(w) + r"[а-яё]{0,3}")
        else:
            core_parts.append(re.escape(w))
    core = r"[\s\-]+".join(core_parts)
    q = f"[{QUOTES}]?"
    prefix = rf"(?:{LEGAL_FORMS}\s*{q}\s*)?"
    suffix = rf"(?:\s*{q}\s*,?\s*{LEGAL_FORMS})?"
    return [rf"{prefix}{q}{core}{q}{suffix}"]


@dataclass(frozen=True)
class Entity:
    kind: str  # EMPLOYEE | SUPPLIER
    id: int
    name: str  # как показываем пользователю

    @property
    def pseudonym(self) -> str:
        return f"{self.kind}-{self.id}"


class Pseudonymizer:
    def __init__(self, employees: dict[int, str], suppliers: dict[int, str]):
        self.entities = [Entity(EMPLOYEE, i, n) for i, n in employees.items()] + [
            Entity(SUPPLIER, i, n) for i, n in suppliers.items()
        ]
        self._by_pseudonym = {e.pseudonym.lower(): e for e in self.entities}
        alternatives: list[tuple[int, str, Entity]] = []
        for e in self.entities:
            patterns = person_patterns(e.name) if e.kind == EMPLOYEE else supplier_patterns(e.name)
            for p in patterns:
                alternatives.append((len(p), p, e))
        # Длинные шаблоны первыми: «Иванова Мария Петровна» раньше, чем «Иванова»
        alternatives.sort(key=lambda a: a[0], reverse=True)
        self._groups: dict[str, Entity] = {}
        parts = []
        for index, (_, pattern, entity) in enumerate(alternatives):
            group = f"g{index}"
            self._groups[group] = entity
            parts.append(f"(?P<{group}>{pattern})")
        self._regex = (
            re.compile(B + "(?:" + "|".join(parts) + ")" + E, re.IGNORECASE) if parts else None
        )

    def mask(self, text: str) -> str:
        if not text or self._regex is None:
            return text
        return self._regex.sub(lambda m: self._groups[m.lastgroup].pseudonym, text)

    def unmask(self, text: str) -> str:
        if not text:
            return text

        def replace(m: re.Match) -> str:
            entity = self._by_pseudonym.get(m.group(0).lower())
            return entity.name if entity else m.group(0)

        return PSEUDONYM_RE.sub(replace, text)

    def stream_unmasker(self) -> "StreamUnmasker":
        return StreamUnmasker(self)


class StreamUnmasker:
    """Обратная замена в потоке: псевдоним может прийти по частям («Поставщ» + «ик-1»).

    Хвост, который может оказаться началом псевдонима, придерживается до следующего
    куска; flush() отдаёт остаток в конце ответа.
    """

    _PREFIXES = (f"{EMPLOYEE}-".lower(), f"{SUPPLIER}-".lower())
    _TAIL = re.compile(rf"({EMPLOYEE}|{SUPPLIER})-\d*$", re.IGNORECASE)

    def __init__(self, pseudonymizer: Pseudonymizer):
        self._p = pseudonymizer
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        cut = self._safe_cut(self._buffer)
        ready, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return self._p.unmask(ready)

    def flush(self) -> str:
        ready, self._buffer = self._buffer, ""
        return self._p.unmask(ready)

    def _safe_cut(self, text: str) -> int:
        tail = self._TAIL.search(text)
        if tail:  # «…Поставщик-1» — номер может продолжиться: «-12»
            return tail.start()
        lower = text.lower()
        for size in range(min(len(lower), max(map(len, self._PREFIXES))), 0, -1):
            end = lower[-size:]
            if any(p.startswith(end) for p in self._PREFIXES):
                return len(text) - size
        return len(text)


async def load_pseudonymizer(session) -> Pseudonymizer:
    """Справочник имён из БД: все сотрудники и поставщики."""
    from sqlalchemy import select

    from app.models import Supplier, User

    employees = dict((await session.execute(select(User.id, User.full_name))).all())
    suppliers = dict((await session.execute(select(Supplier.id, Supplier.name))).all())
    return Pseudonymizer(employees, suppliers)


def mask_items(pii: Pseudonymizer, items: list) -> list:
    """История и вопрос для модели — с псевдонимами вместо имён."""
    from app.llm.types import AssistantMessage, UserMessage

    masked = []
    for item in items:
        if isinstance(item, UserMessage):
            masked.append(UserMessage(pii.mask(item.text)))
        elif isinstance(item, AssistantMessage):
            masked.append(AssistantMessage(pii.mask(item.text)))
        else:
            masked.append(item)
    return masked
