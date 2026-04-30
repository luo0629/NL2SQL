import pytest

from app.agent.graph import reset_agent_graph, run_agent
from app.agent.nodes import _build_sql_plan_prompt, query_understanding
from app.database.executor import SQLExecutor
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable
from app.schemas.sql import SQLExecutionResult
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator


class StubRagService(RagService):
    async def build_query_schema_plan(self, question: str, query_understanding: dict[str, object] | None = None, refresh_schema: bool = False):
        plan = await super().build_query_schema_plan(question)
        return plan


class StubLLMService(LLMService):
    def build_chat_model(self):
        return None


class FakeStructuredModel:
    def invoke(self, prompt: str):
        class Response:
            def __init__(self, content: str):
                self.content = content

        if "query-understanding planner" in prompt:
            return Response('{"intent":"aggregate","target_mentions":["菜品"],"condition_mentions":[{"mention":"状态"}],"value_mentions":["起售"],"order_by":[{"table":"dish","column":"name","direction":"DESC"}],"limit":3,"requires_join_hint":false}')
        return Response('{"from_table":"dish","select":[{"table":"dish","column":"name"}],"order_by":[{"table":"dish","column":"name","direction":"DESC"}],"limit":3}')


class FakeLLMService(LLMService):
    def build_chat_model(self):
        return FakeStructuredModel()


class StubSQLExecutor(SQLExecutor):
    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        return SQLExecutionResult(
            rows=[{"id": 1, "name": "Alice"}],
            row_count=1,
            columns=["id", "name"],
            truncated=False,
            execution_summary="查询执行成功，共返回 1 行。",
        )


class WeakPlanRagService(RagService):
    async def build_query_schema_plan(self, question: str, query_understanding: dict[str, object] | None = None, refresh_schema: bool = False):
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


def _make_dish_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="dish",
                description="菜品表",
                aliases=["菜品", "商品"],
                business_terms=["菜"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True, business_terms=["菜品名称"], semantic_role="dimension"),
                    SchemaColumn(name="price", data_type="DECIMAL", nullable=False, business_terms=["价格", "售价", "销售额"], semantic_role="metric"),
                    SchemaColumn(name="created_at", data_type="TIMESTAMP", nullable=True, semantic_role="timestamp"),
                ],
            ),
            SchemaTable(
                name="flavor",
                description="口味表",
                aliases=["口味", "味道"],
                business_terms=["风味"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True, business_terms=["口味名称"], semantic_role="dimension"),
                    SchemaColumn(name="dish_id", data_type="INTEGER", nullable=False, semantic_role="foreign_key"),
                ],
            ),
        ],
    )


def test_query_understanding_extracts_stable_intent_fields() -> None:
    catalog = _make_dish_catalog()
    state = query_understanding({"question": "查询最近一个月销售额最高的前5个菜品和口味"}, StubLLMService(), catalog)

    understanding = state["query_understanding"]

    assert understanding["intent"] == "ranking"
    assert "菜品" in understanding["target_mentions"]
    assert "口味" in understanding["target_mentions"]
    assert understanding["limit"] == 5
    assert understanding["order_by"] == [{"term": "销售额", "direction": "DESC"}]
    assert understanding["time_range"] == {"type": "relative", "amount": 1, "unit": "月"}
    assert understanding["metrics"][0]["aggregation"] == "SUM"
    assert understanding["requires_join_hint"] is True


def test_query_understanding_uses_llm_when_available() -> None:
    state = query_understanding({"question": "查询起售菜品前三名"}, FakeLLMService())

    understanding = state["query_understanding"]

    assert understanding["source"] == "llm"
    assert understanding["limit"] == 3
    assert understanding["value_mentions"] == ["起售"]
    assert understanding["order_by"] == [{"table": "dish", "column": "name", "direction": "DESC"}]


@pytest.mark.anyio
async def test_agent_graph_prefers_llm_sql_plan_when_available() -> None:
    reset_agent_graph()

    state = await run_agent(
        question="查询起售菜品前三名",
        rag_service=StubRagService(),
        llm_service=FakeLLMService(),
        validator=SQLValidator(),
        executor=StubSQLExecutor(),
    )

    assert state["query_understanding"]["source"] == "llm"
    assert state["sql_plan"]["from_table"] == "dish"
    assert state["sql_plan"]["limit"] == 3
    assert state["sql_plan"]["provenance"]["from_table"] == "schema_linking"


def test_sql_plan_prompt_includes_semantic_brief_and_few_shot_context() -> None:
    prompt = _build_sql_plan_prompt(
        question="统计各分类商品数量",
        schema_context=["Table category: 分类", "Table dish: 菜品"],
        query_understanding={"intent": "aggregate", "dimensions": ["分类"]},
        schema_linking={"linking_summary": "matched category and dish"},
        value_links=[{"mention": "起售", "table": "dish", "column": "status", "db_value": "1"}],
        join_path_plan={"planning_summary": "dish.category_id -> category.id"},
        fallback_plan={"from_table": "dish", "select": []},
        business_semantic_brief={"prompt_block": "Use category as the grouping dimension."},
        few_shot_examples=[{"question": "统计各分类菜品数", "sql": "SELECT category_id, COUNT(*) FROM dish GROUP BY category_id;"}],
    )

    assert "Business semantic brief" in prompt
    assert "Use category as the grouping dimension." in prompt
    assert "Few-shot examples" in prompt
    assert "统计各分类菜品数" in prompt
    assert "dish.category_id -> category.id" in prompt
    assert "起售" in prompt
