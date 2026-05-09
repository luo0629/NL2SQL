from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import configure_middlewares
from app.routers.query import router as query_router
from app.schema_watcher import schema_watcher


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 应用启动阶段初始化日志等基础设施。
    configure_logging()
    settings = get_settings()
    if settings.schema_watcher_enabled:
        await schema_watcher.start(
            databases=settings.effective_database_names,
            interval_seconds=settings.schema_watcher_interval_seconds,
        )
    yield
    await schema_watcher.stop()


def create_application() -> FastAPI:
    # 应用工厂：装配配置、中间件、路由。
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


# ASGI 入口对象，供 uvicorn 加载。
app = create_application()
