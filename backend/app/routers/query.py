from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_agent_service
from app.schemas.query import NLQueryRequest, NLQueryResponse
from app.services.agent_service import AgentService


router = APIRouter(prefix="/api", tags=["query"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "nl2sql-backend"}


@router.post("/query", response_model=NLQueryResponse)
def query_sql(
    payload: NLQueryRequest,
    agent_service: Annotated[AgentService, Depends(get_agent_service)],
) -> NLQueryResponse:
    return agent_service.generate_sql(payload)
