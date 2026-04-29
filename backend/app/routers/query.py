from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_agent_service
from app.schemas.query import NLQueryRequest, NLQueryResponse
from app.services.agent_service import AgentService


# API 路由统一挂在 /api 前缀下。
router = APIRouter(prefix="/api", tags=["query"])


@router.get("/health")
def health_check() -> dict[str, str]:
    # 健康检查：用于本地调试与部署探活。
    return {"status": "ok", "service": "nl2sql-backend"}


@router.post("/query", response_model=NLQueryResponse)
async def query_sql(
    payload: NLQueryRequest,
    agent_service: Annotated[AgentService, Depends(get_agent_service)],
) -> NLQueryResponse:
    # 主入口：接收自然语言问题并返回 SQL 结果。
    return await agent_service.generate_sql(payload)
