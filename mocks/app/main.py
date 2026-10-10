"""Мок-сервисы внешних систем: курсы валют и сервис маркировки."""

from fastapi import FastAPI

from app.marking import router as marking_router
from app.rates import router as rates_router

app = FastAPI(title="External services mock")
app.include_router(rates_router)
app.include_router(marking_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
