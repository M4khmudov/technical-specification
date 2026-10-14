"""Code Interpreter: файлы в контейнер и обратно.

Туда: наборы данных (инструмент export_data) и файлы, загруженные пользователем, —
только с псевдонимами вместо ФИО и поставщиков (в CSV — весь текст, в XLSX — каждая
ячейка). Загруженные копии удаляются у провайдера в конце хода.

Обратно: файлы, созданные кодом модели (графики PNG, CSV, XLSX), забираются из
контейнера, пока он жив, и хранятся у нас. В текстовых файлах псевдонимы заменяются
обратно на настоящие имена; ссылки «sandbox:/mnt/data/…» в ответе — на наши адреса.
"""

import codecs
import io
import logging
import mimetypes
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.pii import Pseudonymizer
from app.core.errors import DomainError
from app.llm.base import FileStore
from app.llm.types import CodeInterpreterSpec
from app.models import ChatFile
from app.models.enums import FileOrigin

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
UPLOAD_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
TEXT_SUFFIXES = {".csv", ".txt", ".md", ".json", ".tsv"}
MAX_GENERATED_BYTES = 10 * 1024 * 1024
SANDBOX_LINK = re.compile(r"sandbox:(/mnt/data/[^\s)\]\"']+)")


# Предел распакованного XLSX: архив в 100 КБ может раскрыться в гигабайты («zip-бомба»)
MAX_XLSX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_XLSX_CELLS = 500_000


def validate_upload(filename: str, data: bytes) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise DomainError("Можно загрузить только таблицу CSV или Excel (.xlsx)")
    if not data:
        raise DomainError("Файл пустой")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DomainError("Файл больше 5 МБ")
    if suffix == ".xlsx":
        _check_xlsx_size(data)
    return UPLOAD_TYPES[suffix]


def _check_xlsx_size(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            unpacked = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise DomainError("Не удалось прочитать файл Excel") from exc
    if unpacked > MAX_XLSX_UNPACKED_BYTES:
        raise DomainError("Файл Excel слишком большой после распаковки (больше 50 МБ)")


# Кодировки CSV: UTF-8 (с BOM и без), UTF-16 (Excel «Юникод-текст»), Windows-1251
_BOMS = [
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
]


def _decode(data: bytes) -> str:
    """Текст CSV. Если кодировку не распознать — отказ, а не «как есть»: иначе имена
    в непрочитанном тексте ушли бы провайдеру без замены."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return _strict(data, encoding)
    if b"\x00" in data:
        # UTF-16 без BOM: нулевые байты на месте старших байтов кириллицы и латиницы
        even_zeros = data[0::2].count(0)
        odd_zeros = data[1::2].count(0)
        return _strict(data, "utf-16-be" if even_zeros > odd_zeros else "utf-16-le")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    text = _strict(data, "cp1251")
    if sum(ch.isprintable() or ch in "\r\n\t" for ch in text) < len(text) * 0.95:
        raise DomainError("Не удалось прочитать CSV: неизвестная кодировка")
    return text


def _strict(data: bytes, encoding: str) -> str:
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise DomainError("Не удалось прочитать CSV: неизвестная кодировка") from exc
    if "\x00" in text:
        raise DomainError("Не удалось прочитать CSV: неизвестная кодировка")
    return text


def mask_file(filename: str, data: bytes, pii: Pseudonymizer | None) -> bytes:
    """Копия файла для провайдера — с псевдонимами вместо имён."""
    if pii is None:
        return data
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return pii.mask(_decode(data)).encode("utf-8")
    if suffix == ".xlsx":
        return _clean_xlsx(data, pii.mask)
    return data


def _clean_xlsx(data: bytes, mask) -> bytes:
    """Новая книга только из значений ячеек — с псевдонимами.

    Править исходный файл ненадёжно: имена живут и вне ячеек — в свойствах документа
    (автор, кто изменил), комментариях и их авторах, колонтитулах, именованных
    диапазонах. Поэтому переносим только листы и значения; формула заменяется
    сохранённым результатом, а если его нет — текстом формулы с псевдонимами."""
    from openpyxl import Workbook, load_workbook

    try:
        formulas = load_workbook(io.BytesIO(data))
        values = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001 — повреждённый или не тот формат
        raise DomainError("Не удалось прочитать файл Excel") from exc
    clean = Workbook()
    clean.remove(clean.active)
    cells = 0
    for sheet, cached in zip(formulas.worksheets, values.worksheets, strict=True):
        target = clean.create_sheet(mask(sheet.title)[:31])
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if value is None:
                    continue
                cells += 1
                if cells > MAX_XLSX_CELLS:
                    raise DomainError("В файле Excel слишком много ячеек")
                if isinstance(value, str) and value.startswith("="):
                    result = cached[cell.coordinate].value
                    value = result if result is not None else value
                if isinstance(value, str):
                    value = mask(value)
                elif not isinstance(value, int | float | Decimal | date | datetime | bool):
                    value = mask(str(value))
                target.cell(row=cell.row, column=cell.column, value=value)
    buffer = io.BytesIO()
    clean.save(buffer)
    return buffer.getvalue()


def unmask_file(filename: str, data: bytes, pii: Pseudonymizer | None) -> bytes:
    """Файл, созданный моделью, — для пользователя: псевдонимы → настоящие имена.
    В картинках текст не заменить — в промпте модель просят не подписывать графики именами."""
    if pii is None:
        return data
    suffix = PurePosixPath(filename).suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            return pii.unmask(_decode(data)).encode("utf-8")
        if suffix == ".xlsx":
            return _map_xlsx(data, pii.unmask)
    except Exception:  # noqa: BLE001 — лучше отдать файл как есть, чем потерять его
        logger.warning("could not unmask generated file %s", suffix)
    return data


def _map_xlsx(data: bytes, transform) -> bytes:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — повреждённый или не тот формат
        raise DomainError("Не удалось прочитать файл Excel") from exc
    for sheet in workbook.worksheets:
        sheet.title = transform(sheet.title)[:31]
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and not cell.value.startswith("="):
                    cell.value = transform(cell.value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@dataclass
class CodeInterpreterSession:
    """Файлы одного хода диалога у провайдера."""

    store: FileStore | None
    pii: Pseudonymizer | None
    file_ids: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    # выполнение кода без файлов (у провайдера нет Files API — например, Groq)
    code_without_files: bool = False

    @property
    def available(self) -> bool:
        """Можно ли класть файлы в контейнер."""
        return self.store is not None

    def spec(self) -> CodeInterpreterSpec | None:
        if self.available or self.code_without_files:
            return CodeInterpreterSpec(file_ids=tuple(self.file_ids))
        return None

    async def add(self, filename: str, data: bytes) -> str:
        """Положить файл в контейнер (с псевдонимами). Возвращает путь внутри контейнера."""
        if self.store is None:
            raise DomainError("Code Interpreter недоступен")
        safe_name = self.pii.mask(filename) if self.pii else filename
        file_id = await self.store.upload_file(safe_name, mask_file(filename, data, self.pii))
        self.file_ids.append(file_id)
        self.filenames.append(safe_name)
        # OpenAI кладёт загруженный файл в контейнер с префиксом идентификатора:
        # /mnt/data/<file_id>-<имя> (проверено на реальном API)
        return f"/mnt/data/{file_id}-{safe_name}"

    async def cleanup(self) -> None:
        if self.store is None:
            return
        for file_id in self.file_ids:
            await self.store.delete_file(file_id)


async def collect_generated(
    session: AsyncSession,
    ci: CodeInterpreterSession,
    container_ids: list[str],
    *,
    conversation_id: int,
    message_id: int,
    user_id: int,
) -> tuple[list[ChatFile], list[str]]:
    """Забрать из контейнеров файлы, созданные кодом модели. Возвращает сохранённые файлы
    и понятные ошибки по тем, что забрать не удалось (ответ при этом не ломается)."""
    saved: list[ChatFile] = []
    problems: list[str] = []
    if ci.store is None:
        return saved, problems
    for container_id in dict.fromkeys(container_ids):
        try:
            files = await ci.store.list_generated_files(container_id)
        except Exception:  # noqa: BLE001 — контейнер мог истечь
            logger.warning("cannot list container files")
            problems.append("Не удалось получить файлы, созданные при расчёте")
            continue
        for file_id, path in files:
            name = PurePosixPath(path).name
            try:
                data = await ci.store.download_container_file(container_id, file_id)
            except Exception:  # noqa: BLE001
                logger.warning("cannot download container file")
                problems.append(f"Не удалось скачать файл {name}")
                continue
            if len(data) > MAX_GENERATED_BYTES:
                problems.append(f"Файл {name} слишком большой")
                continue
            real_name = ci.pii.unmask(name) if ci.pii else name
            chat_file = ChatFile(
                conversation_id=conversation_id,
                message_id=message_id,
                user_id=user_id,
                origin=FileOrigin.GENERATED,
                filename=real_name,
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                size=len(data),
                data=unmask_file(name, data, ci.pii),
            )
            session.add(chat_file)
            saved.append(chat_file)
    if saved:
        await session.commit()
    return saved, problems


def _spellings(name: str) -> set[str]:
    """Как модель может записать имя файла в ссылке: как есть или с %-кодированием
    пробелов и скобок (целиком или частично)."""
    spaces = name.replace(" ", "%20")
    return {
        name,
        spaces,
        spaces.replace("(", "%28").replace(")", "%29"),
        quote(name),
    }


def rewrite_sandbox_links(text: str, files: list[ChatFile], pii: Pseudonymizer | None) -> str:
    """«sandbox:/mnt/data/chart.png» → адрес нашего файла (по имени).

    Сначала — точные имена известных файлов: в них бывают пробелы и скобки
    («график (1).png»), на которых обрывается общее правило ссылки."""
    for f in sorted(files, key=lambda f: len(f.filename), reverse=True):
        names = {f.filename, pii.mask(f.filename) if pii else f.filename}
        for name in names:
            for variant in _spellings(name):
                text = text.replace(f"sandbox:/mnt/data/{variant}", f"/api/chat/files/{f.id}")
    by_name = {f.filename: f for f in files}

    def replace(match: re.Match) -> str:
        name = PurePosixPath(match.group(1)).name
        real = pii.unmask(name) if pii else name
        f = by_name.get(real)
        return f"/api/chat/files/{f.id}" if f else "#файл-недоступен"

    return SANDBOX_LINK.sub(replace, text)
