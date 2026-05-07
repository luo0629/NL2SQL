import asyncio
from types import SimpleNamespace

import pytest

import app.agent.nodes as agent_nodes
from app.agent.graph import reset_agent_graph, run_agent
from app.agent.nodes import async_sql_generator, intent_parser, schema_retriever
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


class TimeoutRecordingSQLExecutor(SQLExecutor):
    def __init__(self) -> None:
        self.explain_timeout_seconds: float | None = None
        self.execute_timeout_seconds: float | None = None
        self.max_rows: int | None = None

    async def explain(
        self,
        sql: str,
        params: list[object] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.explain_timeout_seconds = timeout_seconds

    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        self.execute_timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        return SQLExecutionResult(
            rows=[{"id": 1}],
            row_count=1,
            columns=["id"],
            truncated=False,
            execution_summary="查询执行成功，共返回 1 行。",
        )


class SlowModel:
    async def ainvoke(self, prompt: str):
        await asyncio.sleep(0.05)

        class Response:
            content = "SELECT 1;"

        return Response()


class SlowLLMService(LLMService):
    def build_chat_model(self):
        return SlowModel()


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
    assert "sql_generator" in state["debug_trace"]
    assert "sql_plan" not in state["debug_trace"]


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


@pytest.mark.anyio
async def test_sql_generator_times_out_slow_llm_and_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_nodes,
        "get_settings",
        lambda: SimpleNamespace(agent_llm_node_timeout_seconds=0.001),
    )

    state = await async_sql_generator(
        {"question": "查询菜品", "relevant_tables": ["dish"]},
        SlowLLMService(),
        _make_dish_catalog(),
    )

    assert state["status"] == "mock"
    assert state["used_fallback"] is True
    assert state["debug_trace"]["sql_generator"]["llm_error"] == "timeout"


@pytest.mark.anyio
async def test_agent_graph_passes_timeout_to_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_agent_graph()
    executor = TimeoutRecordingSQLExecutor()
    settings = SimpleNamespace(
        schema_sync_timeout_seconds=8.0,
        sql_explain_timeout_seconds=1.5,
        query_execution_timeout_seconds=2.5,
        query_result_limit=200,
    )
    monkeypatch.setattr("app.agent.graph.get_settings", lambda: settings)
    monkeypatch.setattr(agent_nodes, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_nodes, "_should_run_mysql_explain", lambda: True)

    state = await run_agent(
        question="查询菜品",
        rag_service=StubRagService(),
        llm_service=StubLLMService(),
        validator=SQLValidator(),
        executor=executor,
    )

    assert state["status"] in {"mock", "ready"}
    assert executor.explain_timeout_seconds == 1.5
    assert executor.execute_timeout_seconds == 2.5
    assert executor.max_rows is not None
