from fastapi import FastAPI

from app.api import audit, auth, batches, catalog, chat, errors, health, jobs, marking, memory
from app.core.config import get_settings
from app.core.logging import install_access_log_filter


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, docs_url="/docs", openapi_url="/openapi.json")
    errors.install(app)
    install_access_log_filter()  # в журнал доступа — путь без параметров запроса
    for module in (health, auth, catalog, batches, jobs, audit, marking, chat, memory):
        app.include_router(module.router, prefix="/api")
    return app


app = create_app()
