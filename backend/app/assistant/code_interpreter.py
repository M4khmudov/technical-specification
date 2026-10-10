"""Code Interpreter: файлы в контейнер и обратно.

Туда: наборы данных (инструмент export_data) и файлы, загруженные пользователем, —
только с псевдонимами вместо ФИО и поставщиков (в CSV — весь текст, в XLSX — каждая
ячейка). Загруженные копии удаляются у провайдера в конце хода.

Обратно: файлы, созданные кодом модели (графики PNG, CSV, XLSX), забираются из
контейнера, пока он жив, и хранятся у нас. В текстовых файлах псевдонимы заменяются
обратно на настоящие имена; ссылки «sandbox:/mnt/data/…» в ответе — на наши адреса.
"""

import io
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

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


def validate_upload(filename: str, data: bytes) -> str:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise DomainError("Можно загрузить только таблицу CSV или Excel (.xlsx)")
    if not data:
        raise DomainError("Файл пустой")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DomainError("Файл больше 5 МБ")
    return UPLOAD_TYPES[suffix]


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DomainError("Не удалось прочитать CSV: неизвестная кодировка")


def mask_file(filename: str, data: bytes, pii: Pseudonymizer | None) -> bytes:
    """Копия файла для провайдера — с псевдонимами вместо имён."""
    if pii is None:
        return data
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return pii.mask(_decode(data)).encode("utf-8")
    if suffix == ".xlsx":
        return _map_xlsx(data, pii.mask)
    return data


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

    @property
    def available(self) -> bool:
        return self.store is not None

    def spec(self) -> CodeInterpreterSpec | None:
        return CodeInterpreterSpec(file_ids=tuple(self.file_ids)) if self.available else None

    async def add(self, filename: str, data: bytes) -> str:
        """Положить файл в контейнер (с псевдонимами). Возвращает путь внутри контейнера."""
        if self.store is None:
            raise DomainError("Code Interpreter недоступен")
        safe_name = self.pii.mask(filename) if self.pii else filename
        file_id = await self.store.upload_file(safe_name, mask_file(filename, data, self.pii))
        self.file_ids.append(file_id)
        self.filenames.append(safe_name)
        return f"/mnt/data/{safe_name}"

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


def rewrite_sandbox_links(text: str, files: list[ChatFile], pii: Pseudonymizer | None) -> str:
    """«sandbox:/mnt/data/chart.png» → адрес нашего файла (по имени)."""
    by_name = {f.filename: f for f in files}

    def replace(match: re.Match) -> str:
        name = PurePosixPath(match.group(1)).name
        real = pii.unmask(name) if pii else name
        f = by_name.get(real)
        return f"/api/chat/files/{f.id}" if f else "#файл-недоступен"

    return SANDBOX_LINK.sub(replace, text)
