import asyncio
import logging
import time

from app.agent.graph import run_agent
from app.config import get_settings
from app.database.executor import SQLExecutor
from app.schemas.query import NLQueryRequest, NLQueryResponse
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator


logger = logging.getLogger(__name__)


class AgentService:
    # 通过组合服务的方式封装"自然语言 -> SQL"的完整流程。
    rag_service: RagService
    llm_service: LLMService
    validator: SQLValidator
    sql_executor: SQLExecutor

    def __init__(
        self,
        rag_service: RagService | None = None,
        llm_service: LLMService | None = None,
        validator: SQLValidator | None = None,
        sql_executor: SQLExecutor | None = None,
    ) -> None:
        # 支持依赖注入，便于测试时替换为 mock 实现。
        self.rag_service = rag_service or RagService()
        self.llm_service = llm_service or LLMService()
        self.validator = validator or SQLValidator()
        self.sql_executor = sql_executor or SQLExecutor(validator=self.validator)

    async def generate_sql(self, payload: NLQueryRequest) -> NLQueryResponse:
        # 调用 LangGraph 主流程，执行已在 Graph 内闭环。整体超时要早于前端 60 秒取消。
        settings = get_settings()
        started_at = time.monotonic()
        try:
            state = await asyncio.wait_for(
                run_agent(
                    question=payload.question.strip(),
                    rag_service=self.rag_service,
                    llm_service=self.llm_service,
                    validator=self.validator,
                    executor=self.sql_executor,
                ),
                timeout=settings.agent_request_timeout_seconds,
            )
        except TimeoutError:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            logger.warning(
                "query.agent_timeout timeout_seconds=%.2f duration_ms=%.2f",
                settings.agent_request_timeout_seconds,
                elapsed_ms,
            )
            state = {
                "sql": "",
                "status": "error",
                "rows": [],
                "columns": [],
                "row_count": 0,
                "execution_summary": "查询处理超时，已由后端主动停止。",
                "explanation": "查询处理超时，已由后端主动停止。请稍后重试或缩小查询范围。",
                "execution_time_ms": round(elapsed_ms, 2),
                "debug_trace": {
                    "agent_request": {
                        "status": "timeout",
                        "timeout_seconds": settings.agent_request_timeout_seconds,
                        "duration_ms": round(elapsed_ms, 2),
                    }
                },
            }

        status = state.get("status", "mock")
        sql = state.get("sql") or ("" if status == "error" else "SELECT 1;")
        explanation = (
            state.get("explanation") or "当前返回的是一个教学型 SQLAgent 结果。"
        )

        # 执行结果已由 Graph 内的 execute_sql 节点填充。
        error_message = None
        execution_summary = state.get("execution_summary", "")
        if status == "error":
            error_message = execution_summary

        response = NLQueryResponse(
            sql=sql,
            params=state.get("sql_params", []),
            explanation=explanation,
            status=status,
            rows=state.get("rows", []),
            row_count=state.get("row_count", 0),
            columns=state.get("columns", []),
            execution_summary=execution_summary,
            error_message=error_message,
            execution_time_ms=state.get("execution_time_ms"),
            debug=state.get("debug_trace"),
        )
        logger.info(
            "query_response_summary status=%s used_fallback=%s row_count=%s execution_time_ms=%s",
            status,
            state.get("used_fallback", False),
            response.row_count,
            response.execution_time_ms,
        )
        return response
