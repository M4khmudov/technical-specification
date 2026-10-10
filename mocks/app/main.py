"""Мок-сервисы внешних систем: курсы валют и сервис маркировки.

Логика эндпоинтов добавляется в блоках 3 (курсы) и 6 (маркировка).
"""

from fastapi import FastAPI

app = FastAPI(title="External services mock")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
