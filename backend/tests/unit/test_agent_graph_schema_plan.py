import pytest

from app.agent.graph import reset_agent_graph, run_agent
from app.agent.nodes import intent_parser, schema_retriever
from app.database.executor import SQLExecutor
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable
from app.schemas.sql import SQLExecutionResult
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator


class StubRagService(RagService):
    pass


class StubLLMService(LLMService):
    def build_chat_model(self):
        return None


class FakeStructuredModel:
    def invoke(self, prompt: str):
        class Response:
            def __init__(self, content: str):
                self.content = content

        if "intent_parser" in prompt:
            return Response('{"intent":"查询起售菜品前三名","relevant_tables":["dish","missing_table"]}')
        if "只输出一条 SQL" in prompt:
            return Response("SELECT `id`, `name` FROM `dish` ORDER BY `id` DESC LIMIT 3;")
        return Response("查询执行成功，返回了菜品结果。")


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


class ExplodingSQLExecutor(SQLExecutor):
    async def explain(
        self,
        sql: str,
        params: list[object] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        return None

    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        raise RuntimeError("database_url=mysql://secret should not leak")


class CountingExplodingSQLExecutor(SQLExecutor):
    calls: int

    def __init__(self) -> None:
        self.calls = 0

    async def explain(
        self,
        sql: str,
        params: list[object] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        return None

    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        self.calls += 1
        raise RuntimeError("database_url=mysql://secret should not leak")


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
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True, default="", business_terms=["菜品名称"], semantic_role="dimension"),
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
        relations=[
            SchemaRelation(
                from_table="flavor",
                from_column="dish_id",
                to_table="dish",
                to_column="id",
                relation_type="many-to-one",
            )
        ],
    )


def test_intent_parser_filters_hallucinated_tables() -> None:
    state = intent_parser({"question": "查询起售菜品前三名"}, FakeLLMService(), _make_dish_catalog())

    assert state["intent"] == "查询起售菜品前三名"
    assert state["relevant_tables"] == ["dish"]
    assert "missing_table" not in state["relevant_tables"]


def test_schema_retriever_uses_only_relevant_tables() -> None:
    catalog = _make_dish_catalog()
    state = schema_retriever({"intent": "查询菜品", "relevant_tables": ["dish"]}, catalog)

    assert "Table `dish`" in state["schema_context"]
    assert "Table `flavor`" not in state["schema_context"]
    assert "`flavor`.`dish_id`" not in state["schema_context"]
    assert "`name`" in state["schema_context"]
    assert "default=" in state["schema_context"]


@pytest.mark.anyio
async def test_agent_graph_runs_six_node_pipeline_and_returns_rows() -> None:
    reset_agent_graph()

    state = await run_agent(
        question="查询客户下单状态和用户信息",
        rag_service=StubRagService(),
        llm_service=StubLLMService(),
        validator=SQLValidator(),
        executor=StubSQLExecutor(),
    )

    assert state["intent"]
    assert state["relevant_tables"]
    assert state["schema_context"]
    assert state["sql"].upper().startswith("SELECT")
    assert state["validation_error"] == ""
    assert state["rows"] == [{"id": 1, "name": "Alice"}]
    assert state["final_answer"]
    assert state["debug_trace"]["sql_plan"]["mode"] == "direct_sql_generation"


@pytest.mark.anyio
async def test_agent_graph_uses_llm_for_intent_and_sql_when_available() -> None:
    reset_agent_graph()

    state = await run_agent(
        question="查询起售菜品前三名",
        rag_service=StubRagService(),
        llm_service=FakeLLMService(),
        validator=SQLValidator(),
        executor=StubSQLExecutor(),
    )

    assert state["debug_trace"]["intent_parser"]["source"] == "llm"
    assert "ORDER BY" in state["sql"]
    assert state["status"] == "ready"


@pytest.mark.anyio
async def test_agent_graph_sanitizes_execution_failures() -> None:
    reset_agent_graph()

    state = await run_agent(
        question="查询起售菜品前三名",
        rag_service=StubRagService(),
        llm_service=StubLLMService(),
        validator=SQLValidator(),
        executor=ExplodingSQLExecutor(),
    )

    assert state["status"] == "error"
    assert "RuntimeError" in state["execution_summary"]
    assert "database_url" not in state["execution_summary"]


@pytest.mark.anyio
async def test_agent_graph_does_not_retry_execution_failure_without_model() -> None:
    reset_agent_graph()
    executor = CountingExplodingSQLExecutor()

    state = await run_agent(
        question="找出销量最高的商品",
        rag_service=StubRagService(),
        llm_service=StubLLMService(),
        validator=SQLValidator(),
        executor=executor,
    )

    assert state["status"] == "error"
    assert executor.calls == 1
    assert "database_url" not in state["execution_summary"]
