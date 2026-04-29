from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import configure_middlewares
from app.routers.query import router as query_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 应用启动阶段初始化日志等基础设施。
    configure_logging()
    yield


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
