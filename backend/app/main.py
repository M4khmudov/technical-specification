from fastapi import FastAPI

from app.api import audit, auth, batches, catalog, errors, health, jobs, marking
from app.core.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, docs_url="/docs", openapi_url="/openapi.json")
    errors.install(app)
    for module in (health, auth, catalog, batches, jobs, audit, marking):
        app.include_router(module.router, prefix="/api")
    return app


app = create_app()
