import pytest

from app.agent.graph import reset_agent_graph, run_agent
from app.agent.nodes import query_understanding
from app.database.executor import SQLExecutor
from app.schemas.sql import SQLExecutionResult
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator


class StubRagService(RagService):
    async def build_query_schema_plan(self, question: str):
        plan = await super().build_query_schema_plan(question)
        return plan


class StubLLMService(LLMService):
    def build_chat_model(self):
        return None


class StubSQLExecutor(SQLExecutor):
    async def execute(self, sql: str) -> SQLExecutionResult:
        return SQLExecutionResult(
            rows=[{"id": 1, "name": "Alice"}],
            row_count=1,
            columns=["id", "name"],
            truncated=False,
            execution_summary="查询执行成功，共返回 1 行。",
        )


class WeakPlanRagService(RagService):
    async def build_query_schema_plan(self, question: str):
        plan = await super().build_query_schema_plan(question)
        plan.join_path_plan.plan_confidence = "none"
        plan.join_path_plan.unresolved_tables = ["category"]
        plan.join_path_plan.planning_summary = "主表: user。未解决表: category。规划置信度: none。"
        plan.business_semantic_brief.uncertainties.append("无法可靠连表: category")
        return plan


@pytest.mark.anyio
async def test_agent_graph_populates_schema_plan_stages() -> None:
    reset_agent_graph()

    state = await run_agent(
        question="查询客户下单状态和用户信息",
        rag_service=StubRagService(),
        llm_service=StubLLMService(),
        validator=SQLValidator(),
        executor=StubSQLExecutor(),
    )

    assert state["query_understanding"]
    assert state["query_schema_plan"]
    assert state["schema_linking"]
    assert "value_links" in state
    assert state["join_path_plan"]
    assert state["business_semantic_brief"]
    assert state["sql_plan"]
    assert state["linking_summary"]
    assert state["join_planning_summary"]
    assert state["schema_context"]
    assert "Schema linking：" in state["explanation"]
    assert "Join planning：" in state["explanation"]


@pytest.mark.anyio
async def test_agent_graph_keeps_uncertain_join_plan_visible_in_state() -> None:
    reset_agent_graph()

    state = await run_agent(
        question="查询客户分类和用户信息",
        rag_service=WeakPlanRagService(),
        llm_service=StubLLMService(),
        validator=SQLValidator(),
        executor=StubSQLExecutor(),
    )

    assert state["join_path_plan"]["plan_confidence"] == "none"
    assert state["join_path_plan"]["unresolved_tables"] == ["category"]
    assert "未解决表" in state["join_planning_summary"]
    assert "Join planning：" in state["explanation"]


def test_query_understanding_extracts_stable_intent_fields() -> None:
    state = query_understanding({"question": "查询最近一个月销售额最高的前5个菜品和口味"})

    understanding = state["query_understanding"]

    assert understanding["intent"] == "aggregate"
    assert "菜品" in understanding["target_mentions"]
    assert "口味" in understanding["target_mentions"]
    assert understanding["limit"] == 5
    assert understanding["order_by"] == [{"direction": "DESC"}]
    assert understanding["time_range"] == {"type": "relative"}
    assert understanding["requires_join_hint"] is True
