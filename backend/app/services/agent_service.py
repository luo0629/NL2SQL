from app.agent.graph import run_agent
from app.schemas.query import NLQueryRequest, NLQueryResponse
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator


class AgentService:
    rag_service: RagService
    llm_service: LLMService
    validator: SQLValidator

    def __init__(
        self,
        rag_service: RagService | None = None,
        llm_service: LLMService | None = None,
        validator: SQLValidator | None = None,
    ) -> None:
        self.rag_service = rag_service or RagService()
        self.llm_service = llm_service or LLMService()
        self.validator = validator or SQLValidator()

    def generate_sql(self, payload: NLQueryRequest) -> NLQueryResponse:
        state = run_agent(
            question=payload.question.strip(),
            rag_service=self.rag_service,
            llm_service=self.llm_service,
            validator=self.validator,
        )

        sql = state.get("sql") or "SELECT 1;"
        explanation = (
            state.get("explanation") or "当前返回的是一个教学型 SQLAgent 结果。"
        )
        status = state.get("status", "mock")

        return NLQueryResponse(sql=sql, explanation=explanation, status=status)
