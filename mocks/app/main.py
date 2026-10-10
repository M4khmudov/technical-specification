"""Мок-сервисы внешних систем: курсы валют и сервис маркировки.

Логика маркировки добавляется в блоке 6.
"""

from fastapi import FastAPI

from app.rates import router as rates_router

app = FastAPI(title="External services mock")
app.include_router(rates_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
