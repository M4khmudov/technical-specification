"""Логи без персональных данных.

В строке запроса бывают названия поставщиков и ФИО (фильтр реестра партий по
поставщику, в том числе ИП). Журнал доступа uvicorn пишет путь целиком — оставляем
в нём только путь без параметров.
"""

import logging


class StripQueryFilter(logging.Filter):
    """Убирает «?…» из пути в записи журнала доступа uvicorn."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def install_access_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, StripQueryFilter) for f in access.filters):
        access.addFilter(StripQueryFilter())
