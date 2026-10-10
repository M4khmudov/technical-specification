from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import auth, batches, catalog, health, jobs
from app.core.config import get_settings
from app.core.errors import DomainError


async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, docs_url="/docs", openapi_url="/openapi.json")
    app.add_exception_handler(DomainError, domain_error_handler)
    for module in (health, auth, catalog, batches, jobs):
        app.include_router(module.router, prefix="/api")
    return app


app = create_app()
