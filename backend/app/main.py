from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import configure_middlewares
from app.routers.query import router as query_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    yield


def create_application() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        description="Structured backend starter for an NL2SQL agent.",
        version="0.1.0",
        lifespan=lifespan,
    )
    configure_middlewares(application, settings)
    application.include_router(query_router)
    return application


app = create_application()
