from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.graph import build_agent_graph
from app.agent.nodes import value_validator
from app.agent.value_validation import extract_value_predicates
from app.rag.business_semantics import attach_business_semantics
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable
from app.schemas.sql import SQLExecutionResult
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator


def _catalog() -> SchemaCatalog:
    return attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="dish",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True),
                    SchemaColumn(name="category", data_type="VARCHAR", nullable=True),
                    SchemaColumn(name="status", data_type="INTEGER", nullable=False, description="0 未上架 1 起售"),
                ],
            ),
            SchemaTable(
                name="shop",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True),
                ],
            ),
        ],
    ))


def test_extract_value_predicates_supports_eq_in_and_aliases() -> None:
    predicates = extract_value_predicates(
        "SELECT * FROM `dish` d WHERE d.`name` = '牛肉' AND d.`category` IN ('川菜', '粤菜') AND d.`id` = '1'",
        _catalog(),
    )

    assert [(item.table, item.column, item.value, item.operator) for item in predicates] == [
        ("dish", "name", "牛肉", "="),
        ("dish", "category", "川菜", "IN"),
        ("dish", "category", "粤菜", "IN"),
    ]


def test_extract_value_predicates_skips_ambiguous_or_and_non_string_columns() -> None:
    catalog = _catalog()

    assert extract_value_predicates("SELECT * FROM `dish` WHERE `name` = '牛肉' OR `category` = '川菜'", catalog) == []
    assert extract_value_predicates("SELECT * FROM `dish`, `shop` WHERE `name` = '牛肉'", catalog) == []
    assert extract_value_predicates("SELECT * FROM `dish` WHERE `status` = '起售'", catalog) == []


def test_extract_value_predicates_resolves_database_qualified_tables_and_aliases() -> None:
    catalog = SchemaCatalog(
        database="jc_config,jc_experimental",
        tables=[
            SchemaTable(
                database="jc_config",
                name="employee",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True),
                ],
            ),
            SchemaTable(
                database="jc_experimental",
                name="employee",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True),
                ],
            ),
        ],
    )

    predicates = extract_value_predicates(
        "SELECT * FROM `jc_config`.`employee` e WHERE e.`name` = '张三'",
        catalog,
    )

    assert [(item.table, item.column, item.value) for item in predicates] == [("jc_config.employee", "name", "张三")]
    assert extract_value_predicates("SELECT * FROM `employee` WHERE `name` = '张三'", catalog) == []


class FakeValueProbeExecutor:
    def __init__(self, existing: set[tuple[str, str, str]] | None = None) -> None:
        self.existing = existing or set()
        self.exists_calls: list[tuple[str, str, str]] = []
        self.suggestion_calls: list[tuple[str, str, str]] = []
        self.executed_sql: list[str] = []

    async def explain(
        self,
        sql: str,
        params: list[object] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        return None

    async def value_exists(
        self,
        table: str,
        column: str,
        value: str,
        timeout_seconds: float | None = None,
    ) -> bool:
        self.exists_calls.append((table, column, value))
        return (table, column, value) in self.existing

    async def suggest_similar_values(
        self,
        table: str,
        column: str,
        value: str,
        limit: int = 5,
        timeout_seconds: float | None = None,
    ) -> list[str]:
        self.suggestion_calls.append((table, column, value))
        return [f"{value}饭"]

    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        self.executed_sql.append(sql)
        return SQLExecutionResult(
            rows=[{"name": "牛肉饭"}],
            row_count=1,
            columns=["name"],
            truncated=False,
            execution_summary="查询执行成功，共返回 1 行。",
        )


@pytest.mark.anyio
async def test_value_validator_sets_retry_error_for_missing_value() -> None:
    executor = FakeValueProbeExecutor()

    result = await value_validator(
        {
            "question": "查询牛肉菜品",
            "sql": "SELECT `name` FROM `dish` WHERE `name` = '牛肉';",
            "retry_count": 0,
            "max_retries": 3,
        },
        executor,
        _catalog(),
    )

    assert result["retry_count"] == 1
    assert "SQL 值存在性校验未通过" in result["validation_error"]
    assert "`dish`.`name` 中不存在值 '牛肉'" in result["validation_error"]
    assert "牛肉饭" in result["validation_error"]
    assert result["validation_issues"][0]["code"] == "VALUE_NOT_FOUND"
    assert executor.exists_calls == [("dish", "name", "牛肉")]
    assert executor.suggestion_calls == [("dish", "name", "牛肉")]


class RepairingModel:
    def invoke(self, prompt: str):
        if "intent_parser" in prompt:
            return SimpleNamespace(content='{"intent":"查询牛肉菜品","relevant_tables":["dish"]}')
        if "只输出一条 SQL" in prompt:
            if "SQL 值存在性校验未通过" in prompt:
                return SimpleNamespace(content="SELECT `name` FROM `dish` WHERE `name` = '牛肉饭' ORDER BY `name` LIMIT 200;")
            return SimpleNamespace(content="SELECT `name` FROM `dish` WHERE `name` = '牛肉' ORDER BY `name` LIMIT 200;")
        return SimpleNamespace(content="查询执行成功。")


class RepairingLLMService(LLMService):
    def build_chat_model(self):
        return RepairingModel()


class StubRagService(RagService):
    pass


@pytest.mark.anyio
async def test_graph_retries_when_value_validator_finds_missing_value() -> None:
    executor = FakeValueProbeExecutor(existing={("dish", "name", "牛肉饭")})
    graph = build_agent_graph(
        StubRagService(),
        RepairingLLMService(),
        SQLValidator(),
        executor,
        _catalog(),
    )

    state = await graph.ainvoke({
        "question": "查询牛肉菜品",
        "user_input": "查询牛肉菜品",
        "retry_count": 0,
        "max_retries": 3,
    })

    assert state["retry_count"] == 1
    assert state["status"] == "ready"
    assert executor.executed_sql == ["SELECT `name` FROM `dish` WHERE `name` = '牛肉饭' ORDER BY `name` LIMIT 200;"]
    assert executor.exists_calls == [("dish", "name", "牛肉"), ("dish", "name", "牛肉饭")]
    assert state["debug_trace"]["value_validator"]["status"] == "passed"
