import asyncio
import logging
from types import SimpleNamespace

import pytest

import app.agent.nodes as agent_nodes
from app.agent.graph import build_agent_graph, reset_agent_graph, run_agent
from app.agent.nodes import async_sql_generator, build_fallback_sql, intent_parser, schema_retriever
from app.database.executor import SQLExecutor
from app.rag.business_semantics import attach_business_semantics
from app.rag.schema_governance import build_relationship_graph_artifact
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


class JoinRepairModel:
    def __init__(self) -> None:
        self.sql_prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        class Response:
            def __init__(self, content: str):
                self.content = content

        if "只返回一个 JSON 对象" in prompt:
            return Response('{"intent":"查询订单付款","relevant_tables":["orders","payments"]}')

        if "只输出一条 SQL" in prompt:
            self.sql_prompts.append(prompt)
            if "检测到当前 JOIN 选择了较弱候选" in prompt:
                return Response(
                    "SELECT `orders`.`order_no`, `payments`.`pay_amount` FROM `orders` "
                    "JOIN `payments` ON `orders`.`order_no` = `payments`.`order_no` "
                    "ORDER BY `orders`.`id` DESC LIMIT 20;"
                )
            return Response(
                "SELECT `orders`.`order_no`, `payments`.`pay_amount` FROM `orders` "
                "JOIN `payments` ON `orders`.`trace_no` = `payments`.`trace_no` "
                "ORDER BY `orders`.`id` DESC LIMIT 20;"
            )

        return Response("查询执行成功，返回了订单付款结果。")


class JoinRepairLLMService(LLMService):
    def __init__(self) -> None:
        self.model = JoinRepairModel()

    def build_chat_model(self):
        return self.model


class CountRepairModel:
    def __init__(self) -> None:
        self.sql_prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        class Response:
            def __init__(self, content: str):
                self.content = content

        if "只返回一个 JSON 对象" in prompt:
            return Response('{"intent":"统计订单数量","relevant_tables":["orders"]}')

        if "只输出一条 SQL" in prompt:
            self.sql_prompts.append(prompt)
            if "检测到当前统计口径默认依赖了技术主键/技术外键" in prompt:
                return Response("SELECT COUNT(`order_no`) AS `order_count` FROM `orders`; ")
            return Response("SELECT COUNT(`id`) AS `order_count` FROM `orders`;")

        return Response("查询执行成功，返回了订单数量结果。")


class CountRepairLLMService(LLMService):
    def __init__(self) -> None:
        self.model = CountRepairModel()

    def build_chat_model(self):
        return self.model


class StubSQLExecutor(SQLExecutor):
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


class JoinAwareSQLExecutor(SQLExecutor):
    def __init__(self) -> None:
        self.executed_sql: list[str] = []

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
        self.executed_sql.append(sql)
        if "`trace_no` = `payments`.`trace_no`" in sql:
            return SQLExecutionResult(
                rows=[],
                row_count=0,
                columns=["order_no", "pay_amount"],
                truncated=False,
                execution_summary="查询执行成功，但没有返回记录。",
            )
        return SQLExecutionResult(
            rows=[{"order_no": "O1001", "pay_amount": 88.0}],
            row_count=1,
            columns=["order_no", "pay_amount"],
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
    return attach_business_semantics(SchemaCatalog(
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
                    SchemaColumn(name="status", data_type="INTEGER", nullable=False, description="0 未上架 1 起售", business_terms=["菜品状态"], semantic_role="dimension"),
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
    ))


def _make_soft_delete_catalog() -> SchemaCatalog:
    return attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="orders",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, semantic_role="dimension", business_terms=["订单号"]),
                    SchemaColumn(name="deleted", data_type="TINYINT", nullable=False, description="软删除标记", semantic_role="internal"),
                    SchemaColumn(name="created_at", data_type="DATETIME", nullable=True, semantic_role="timestamp"),
                ],
            )
        ],
    ))


def _make_count_catalog() -> SchemaCatalog:
    return attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="orders",
                description="订单表",
                business_terms=["订单"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="业务订单编号", semantic_role="dimension", business_terms=["订单号", "单号"]),
                    SchemaColumn(name="customer_name", data_type="VARCHAR", nullable=True, description="客户名称", semantic_role="dimension", business_terms=["客户"]),
                    SchemaColumn(name="created_at", data_type="DATETIME", nullable=True, semantic_role="timestamp", description="下单时间"),
                ],
            )
        ],
    ))


def _make_join_repair_catalog(tmp_path, *, qualified: bool = False) -> SchemaCatalog:
    relation_kwargs = {"from_database": "sales", "to_database": "sales"} if qualified else {}
    table_database = "sales" if qualified else None
    catalog = attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                database=table_database,
                name="orders",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="业务订单编号", semantic_role="dimension", cross_table_diff="订单主编号，优先作为跨表关联键"),
                    SchemaColumn(name="trace_no", data_type="VARCHAR", nullable=True, description="临时追踪号，可能为空，仅用于内部排障", semantic_role="dimension", cross_table_diff="临时追踪号，不能优先作为跨表关联键"),
                ],
            ),
            SchemaTable(
                database=table_database,
                name="payments",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="业务订单编号", semantic_role="dimension", cross_table_diff="付款记录对应订单主编号，优先关联 orders.order_no"),
                    SchemaColumn(name="trace_no", data_type="VARCHAR", nullable=True, description="临时追踪号，保留字段", semantic_role="dimension", cross_table_diff="保留字段，高空值，避免优先联表"),
                    SchemaColumn(name="pay_amount", data_type="DECIMAL", nullable=False, description="付款金额", semantic_role="metric"),
                ],
            ),
        ],
        relations=[
            SchemaRelation(
                from_table="payments",
                from_column="order_no",
                to_table="orders",
                to_column="order_no",
                relation_type="inferred-shared-key",
                confidence="high",
                join_hint="优先按业务订单编号联表",
                ranking_score=9.8,
                validation_summary="sample_probe(rows=4/4; non_null=1.00/1.00; distinct=1.00/1.00; overlap=0.75)",
                **relation_kwargs,
            ),
            SchemaRelation(
                from_table="payments",
                from_column="trace_no",
                to_table="orders",
                to_column="trace_no",
                relation_type="inferred-shared-key",
                confidence="low",
                join_hint="仅排障时使用临时追踪号，不应优先联表",
                ranking_score=1.2,
                validation_summary="sample_probe(rows=4/4; non_null=0.25/0.50; distinct=0.33/0.33; overlap=0.00)",
                **relation_kwargs,
            ),
        ],
    ))
    catalog.relationship_graph = build_relationship_graph_artifact(
        catalog,
        scope_key="sqlite+aiosqlite:///join-repair-test",
        artifact_dir=tmp_path,
        generated_at="2026-05-10T00:00:00Z",
    )
    return catalog


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


def test_schema_retriever_appends_conversational_enum_mapping_to_field_comment() -> None:
    catalog = _make_dish_catalog()
    state = schema_retriever({"question": "查询起售菜品", "relevant_tables": ["dish"]}, catalog)

    assert "`status`" in state["schema_context"]
    assert "comment=0 未上架 1 起售; enum_mapping: 未上架=0, 起售=1" in state["schema_context"]
    assert "mapping=起售=1" in state["semantic_context"] or "mapping=未上架=0, 起售=1" in state["semantic_context"]


def test_schema_retriever_includes_business_semantic_context_without_polluting_schema() -> None:
    catalog = _make_dish_catalog()
    state = schema_retriever({"question": "查询销售额最高的商品", "relevant_tables": ["dish"]}, catalog)

    assert "Business semantics:" not in state["schema_context"]
    assert "销售额" in state["semantic_context"]
    assert "口味名称" not in state["semantic_context"]
    assert state["debug_trace"]["schema_retriever"]["semantic_context_chars"] > 0


def test_intent_parser_uses_semantic_terms_for_fallback_table_selection() -> None:
    state = intent_parser({"question": "查询销售额最高的商品"}, StubLLMService(), _make_dish_catalog())

    assert state["relevant_tables"][0] == "dish"
    assert any(signal["term"] == "销售额" for signal in state["semantic_signals"])


def test_schema_retriever_keeps_join_relations_in_schema_context() -> None:
    catalog = _make_dish_catalog()
    state = schema_retriever({"question": "查询菜品口味", "relevant_tables": ["dish", "flavor"]}, catalog)

    assert "Business semantics:" not in state["schema_context"]
    assert "Relations:" in state["schema_context"]
    assert "`flavor`.`dish_id` -> `dish`.`id`" in state["schema_context"]


def test_intent_parser_logs_table_selection(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        state = intent_parser({"question": "查询起售菜品前三名"}, FakeLLMService(), _make_dish_catalog())

    assert state["relevant_tables"] == ["dish"]
    assert "agent.intent_parser.selection" in caplog.text
    assert "relevant_tables=['dish']" in caplog.text
    assert "candidate_scores=['dish:" in caplog.text


def test_schema_retriever_logs_join_relations(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        state = schema_retriever({"question": "查询菜品口味", "relevant_tables": ["dish", "flavor"]}, _make_dish_catalog())

    assert "agent.schema_retriever.selection" in caplog.text
    assert "flavor.dish_id -> dish.id (many-to-one)" in caplog.text
    assert state["relevant_tables"] == ["dish", "flavor"]


def test_schema_context_marks_ids_as_join_internal_but_keeps_them_visible() -> None:
    catalog = _make_dish_catalog()
    state = schema_retriever({"question": "查询菜品口味", "relevant_tables": ["dish", "flavor"]}, catalog)

    assert "Preferred SELECT output columns: `name`, `price`" in state["schema_context"]
    assert "`id` (INTEGER; NOT NULL; PRIMARY KEY; output=internal identifier; do not select by default)" in state["schema_context"]
    assert "`dish_id` (INTEGER; NOT NULL; role=foreign_key; output=join/filter/internal; do not select by default)" in state["schema_context"]
    assert "`flavor`.`dish_id` -> `dish`.`id`" in state["schema_context"]


def test_schema_context_exposes_cross_table_diff_guidance() -> None:
    catalog = attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="orders",
                columns=[
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, cross_table_diff="业务主编号，可作为跨表关联键", semantic_role="dimension"),
                ],
            ),
            SchemaTable(
                name="payments",
                columns=[
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, cross_table_diff="业务主编号，可作为跨表关联键", semantic_role="dimension"),
                ],
            ),
        ],
        relations=[
            SchemaRelation(
                from_table="payments",
                from_column="order_no",
                to_table="orders",
                to_column="order_no",
                relation_type="inferred-shared-key",
                confidence="medium",
                join_hint="优先按业务主编号联表",
                ranking_score=8.5,
                validation_summary="sample_probe(rows=4/4; non_null=1.00/1.00; distinct=1.00/1.00; overlap=0.75)",
            )
        ],
    ))

    state = schema_retriever({"question": "查询订单付款", "relevant_tables": ["orders", "payments"]}, catalog)

    assert "cross_table_diff=业务主编号，可作为跨表关联键" in state["schema_context"]
    assert "confidence=medium" in state["schema_context"]
    assert "score=8.50" in state["schema_context"]
    assert "validation=sample_probe(rows=4/4; non_null=1.00/1.00; distinct=1.00/1.00; overlap=0.75)" in state["schema_context"]
    assert "hint=优先按业务主编号联表" in state["schema_context"]
    assert state["debug_trace"]["schema_retriever"]["relation_signals"]


def test_schema_context_surfaces_preferred_and_weaker_join_candidates(tmp_path) -> None:
    state = schema_retriever(
        {"question": "查询订单付款", "relevant_tables": ["orders", "payments"]},
        _make_join_repair_catalog(tmp_path),
    )

    assert "Preferred join candidates:" in state["schema_context"]
    assert "Avoid weaker join candidates:" in state["schema_context"]
    assert "`payments`.`order_no` = `orders`.`order_no`" in state["schema_context"]
    assert "`payments`.`trace_no` = `orders`.`trace_no`" in state["schema_context"]
    assert "suspected_endpoint" in state["schema_context"]


def test_sql_generation_prompt_guides_field_matching_rules() -> None:
    prompt = agent_nodes._build_sql_generation_prompt({
        "question": "查询起售菜品名称包含牛肉的记录",
        "schema_context": "Table `dish`\n- `name` (VARCHAR; comment=菜品名称)\n- `status` (INTEGER; comment=0 未上架 1 起售; enum_mapping: 未上架=0, 起售=1)",
        "semantic_context": "(无)",
        "relevant_tables": ["dish"],
    })

    assert "带 enum_mapping/枚举对照的字段必须使用精确匹配" in prompt
    assert "匹配值只能来自 schema_context 中该字段的 enum_mapping" in prompt
    assert "JOIN 规则：优先使用 schema_context 中 Preferred join candidates、Relations、Table Relations、hint、confidence、preferred_score 明确推荐的联表键" in prompt
    assert "JOIN 类型规则：先识别用户问题的主查询对象" in prompt
    assert "右表筛选条件必须写在 ON 中" in prompt
    assert "名称类字符串字段" in prompt
    assert "默认使用 LIKE 模糊匹配" in prompt
    assert "字段类型或业务含义不确定时，优先使用 LIKE" in prompt
    assert "COUNT 口径规则" in prompt
    assert "不要默认写 COUNT(`id`)" in prompt
    assert "软删除规则：如果相关表存在 `deleted` 字段" in prompt
    assert "必须使用 MySQL 全限定表名" in prompt
    assert "`database_name`.`table_name`" in prompt
    assert "`jc_config`.`table`" not in prompt
    assert "`jc_experimental`.`table`" not in prompt


def test_left_join_where_collapse_message_rejects_right_table_filter() -> None:
    sql = (
        "SELECT `customers`.`name`, `orders`.`amount` "
        "FROM `customers` LEFT JOIN `orders` ON `customers`.`id` = `orders`.`customer_id` "
        "WHERE `orders`.`status` = 'paid' ORDER BY `customers`.`id` DESC LIMIT 20"
    )

    message = agent_nodes._left_join_where_collapse_message(sql)

    assert message is not None
    assert "LEFT JOIN 右表字段出现在 WHERE" in message
    assert "orders.status" in message


def test_left_join_where_collapse_message_allows_missing_association_check() -> None:
    sql = (
        "SELECT `customers`.`name` FROM `customers` "
        "LEFT JOIN `orders` AS `o` ON `customers`.`id` = `o`.`customer_id` "
        "WHERE `o`.`id` IS NULL ORDER BY `customers`.`id` DESC LIMIT 20"
    )

    assert agent_nodes._left_join_where_collapse_message(sql) is None


@pytest.mark.anyio
async def test_sql_validator_retries_left_join_right_table_where_filter() -> None:
    state = {
        "generated_sql": (
            "SELECT `customers`.`name`, `o`.`amount` FROM `customers` "
            "LEFT JOIN `orders` AS `o` ON `customers`.`id` = `o`.`customer_id` "
            "WHERE `o`.`status` = 'paid' ORDER BY `customers`.`id` DESC LIMIT 20"
        ),
        "retry_count": 0,
        "max_retries": 3,
    }

    result = await agent_nodes.sql_validator(state, SQLValidator(), StubSQLExecutor())

    assert result["retry_count"] == 1
    assert "LEFT JOIN 右表字段出现在 WHERE" in result["validation_error"]
    assert result["validation_issues"][0]["repairable"] is True


def test_schema_context_exposes_preferred_count_expression_for_count_questions() -> None:
    state = schema_retriever({"question": "订单数量是多少", "relevant_tables": ["orders"]}, _make_count_catalog())

    assert "Preferred COUNT expression: COUNT(`order_no`)" in state["schema_context"]
    assert "避免默认使用技术主键 `id`" in state["schema_context"]


FIELD_EXAMPLES_CONFIG = {
    "tables": {
        "jzjc.weituo": {
            "fields": {
                "wtbh": {
                    "aliases": ["委托编号", "委托单号"],
                    "examples": [
                        {
                            "question": "查询委托编号为 WT2024-001 的委托",
                            "sql_pattern": "WHERE `wtbh` = 'WT2024-001'",
                        }
                    ],
                },
                "wt_org": {
                    "aliases": ["委托单位", "送检单位"],
                    "examples": [
                        {
                            "question": "查询委托单位包含 建工 的委托",
                            "sql_pattern": "WHERE `wt_org` LIKE '%建工%'",
                        }
                    ],
                },
            }
        },
        "jzjc.acceptance_slip": {
            "fields": {
                "sldbh": {
                    "aliases": ["受理单编号"],
                    "examples": [
                        {
                            "question": "查询受理单编号为 SLD2024-001 的记录",
                            "sql_pattern": "WHERE `sldbh` = 'SLD2024-001'",
                        }
                    ],
                }
            }
        },
    }
}


def test_sql_generation_prompt_injects_only_matching_field_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.config_loader.get_app_config",
        lambda: SimpleNamespace(field_examples=FIELD_EXAMPLES_CONFIG),
    )

    prompt = agent_nodes._build_sql_generation_prompt({
        "question": "查询委托单位包含建工的委托",
        "schema_context": "Table `jzjc`.`weituo`\n- `wt_org` (VARCHAR; comment=委托单位)\n- `wtbh` (VARCHAR; comment=委托编号)",
        "semantic_context": "(无)",
        "relevant_tables": ["jzjc.weituo"],
    })

    assert "field_example_context:" in prompt
    assert "Matched field example hints" in prompt
    assert "`jzjc.weituo`.`wt_org`" in prompt
    assert "委托单位包含 建工" in prompt
    assert "`jzjc.acceptance_slip`.`sldbh`" not in prompt


def test_sql_generation_prompt_skips_field_examples_without_question_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.config_loader.get_app_config",
        lambda: SimpleNamespace(field_examples=FIELD_EXAMPLES_CONFIG),
    )

    prompt = agent_nodes._build_sql_generation_prompt({
        "question": "统计最近创建的委托记录数",
        "schema_context": "Table `jzjc`.`weituo`\n- `create_time` (DATETIME)\n- `wtbh` (VARCHAR; comment=委托编号)",
        "semantic_context": "(无)",
        "relevant_tables": ["jzjc.weituo"],
    })

    assert "field_example_context:" in prompt
    assert "(无匹配字段示例)" in prompt
    assert "`jzjc.weituo`.`wtbh`" not in prompt


def test_fallback_sql_prefers_display_columns_over_bare_id() -> None:
    sql = build_fallback_sql("查询菜品", _make_dish_catalog(), ["dish"])

    select_clause = sql.split(" FROM ", 1)[0]
    assert select_clause == "SELECT `name`, `price`, `status`"
    assert "`id`" not in select_clause
    assert "ORDER BY `created_at` DESC, `id` DESC" in sql


def test_fallback_sql_prefers_semantic_time_field_for_ordering() -> None:
    sql = build_fallback_sql("查询最新菜品", _make_dish_catalog(), ["dish"])

    assert "ORDER BY `created_at` DESC, `id` DESC" in sql


def test_fallback_sql_uses_field_semantics_for_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="news",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="headline", data_type="VARCHAR", nullable=True, semantic_role="dimension"),
                    SchemaColumn(name="published_on", data_type="DATETIME", nullable=True),
                ],
            )
        ],
    ))

    monkeypatch.setattr(
        "app.config_loader.get_app_config",
        lambda: SimpleNamespace(
            field_semantics={
                "fields": {
                    "news": {
                        "published_on": {
                            "semantic_role": "timestamp",
                            "business_terms": ["发布时间"],
                        }
                    }
                }
            },
            agent_strategy={},
        ),
    )

    sql = build_fallback_sql("查询最新公告", catalog, ["news"])

    assert "ORDER BY `published_on` DESC, `id` DESC" in sql


def test_fallback_sql_prefers_business_identifier_when_no_explicit_time_intent() -> None:
    catalog = attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="orders",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="业务订单编号", semantic_role="dimension", business_terms=["订单号", "单号"]),
                    SchemaColumn(name="created_at", data_type="DATETIME", nullable=True, semantic_role="timestamp", description="下单时间"),
                    SchemaColumn(name="customer_name", data_type="VARCHAR", nullable=True, semantic_role="dimension"),
                ],
            )
        ],
    ))

    sql = build_fallback_sql("查询订单", catalog, ["orders"])

    assert "ORDER BY `order_no` DESC, `id` DESC" in sql


def test_fallback_sql_defaults_to_active_records_for_deleted_column() -> None:
    sql = build_fallback_sql("查询订单", _make_soft_delete_catalog(), ["orders"])

    assert "WHERE `deleted` = 0" in sql


def test_fallback_sql_switches_to_deleted_records_when_user_asks_explicitly() -> None:
    sql = build_fallback_sql("查询已删除记录", _make_soft_delete_catalog(), ["orders"])

    assert "WHERE `deleted` = 1" in sql
    assert "WHERE `deleted` = 0" not in sql


def test_fallback_sql_allows_all_records_when_user_requests_including_deleted() -> None:
    sql = build_fallback_sql("查询全部订单，包含已删除数据", _make_soft_delete_catalog(), ["orders"])

    assert "WHERE `deleted` = 0" not in sql
    assert "WHERE `deleted` = 1" not in sql


def test_fallback_sql_falls_back_to_primary_key_when_no_better_signal() -> None:
    catalog = attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="events",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="payload", data_type="TEXT", nullable=True),
                ],
            )
        ],
    ))

    sql = build_fallback_sql("查询事件", catalog, ["events"])

    assert "ORDER BY `id` DESC" in sql


def test_fallback_sql_honors_table_level_order_override(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = attach_business_semantics(SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="tickets",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="priority_rank", data_type="INTEGER", nullable=False, semantic_role="metric"),
                    SchemaColumn(name="title", data_type="VARCHAR", nullable=True, semantic_role="dimension"),
                ],
            )
        ],
    ))

    monkeypatch.setattr(
        "app.config_loader.get_app_config",
        lambda: SimpleNamespace(
            field_semantics={},
            agent_strategy={
                "fallback": {
                    "order_by": {
                        "tables": {
                            "tickets": {
                                "column": "priority_rank",
                                "direction": "asc",
                            }
                        }
                    }
                }
            },
        ),
    )

    sql = build_fallback_sql("查询工单", catalog, ["tickets"])

    assert "ORDER BY `priority_rank` ASC, `id` ASC" in sql


def test_fallback_sql_allows_identifier_when_user_explicitly_asks() -> None:
    sql = build_fallback_sql("查询菜品ID和名称", _make_dish_catalog(), ["dish"])

    assert sql.startswith("SELECT `id`, `name`, `price`, `status`, `created_at` FROM `dish`")


def test_schema_context_and_fallback_sql_use_qualified_table_names() -> None:
    catalog = attach_business_semantics(SchemaCatalog(
        database="jc_config,jc_experimental",
        tables=[
            SchemaTable(
                database="jc_config",
                name="employee",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True, semantic_role="dimension"),
                ],
            ),
            SchemaTable(
                database="jc_experimental",
                name="employee",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="experiment_name", data_type="VARCHAR", nullable=True, semantic_role="dimension"),
                ],
            ),
        ],
        relations=[
            SchemaRelation(
                from_database="jc_experimental",
                from_table="employee",
                from_column="id",
                to_database="jc_config",
                to_table="employee",
                to_column="id",
                relation_type="foreign_key",
            )
        ],
    ))

    state = schema_retriever({"question": "查询员工实验", "relevant_tables": ["jc_config.employee", "jc_experimental.employee"]}, catalog)
    sql = build_fallback_sql("查询员工", catalog, ["jc_config.employee"])

    assert "Table `jc_config`.`employee`" in state["schema_context"]
    assert "Table `jc_experimental`.`employee`" in state["schema_context"]
    assert "`jc_experimental`.`employee`.`id` -> `jc_config`.`employee`.`id`" in state["schema_context"]
    assert "FROM `jc_config`.`employee`" in sql


def test_join_repair_detects_weaker_candidate_with_unqualified_sql_on_qualified_catalog(tmp_path) -> None:
    catalog = _make_join_repair_catalog(tmp_path, qualified=True)
    sql = (
        "SELECT `orders`.`order_no`, `payments`.`pay_amount` FROM `orders` "
        "JOIN `payments` ON `orders`.`trace_no` = `payments`.`trace_no` "
        "ORDER BY `orders`.`id` DESC LIMIT 20;"
    )

    message = agent_nodes._best_alternative_join_message(sql, catalog)

    assert message is not None
    assert "`orders`.`trace_no` = `payments`.`trace_no`" in message
    assert "`sales`.`payments`.`order_no` = `sales`.`orders`.`order_no`" in message


def test_count_selection_validation_rejects_count_id_for_business_entity_question() -> None:
    message = agent_nodes._count_selection_validation_message(
        "SELECT COUNT(`id`) AS `order_count` FROM `orders`;",
        {
            "question": "订单数量是多少",
            "relevant_tables": ["orders"],
            "schema_catalog": _make_count_catalog(),
        },
    )

    assert message is not None
    assert "COUNT(`id`)" in message
    assert "COUNT(`order_no`)" in message


@pytest.mark.anyio
async def test_agent_graph_repairs_weaker_join_before_execution(tmp_path) -> None:
    llm_service = JoinRepairLLMService()
    executor = JoinAwareSQLExecutor()
    graph = build_agent_graph(
        StubRagService(),
        llm_service,
        SQLValidator(),
        executor,
        catalog=_make_join_repair_catalog(tmp_path),
    )

    state = await graph.ainvoke({"user_input": "查询订单付款", "retry_count": 0, "max_retries": 3})

    assert state["status"] == "ready"
    assert state["row_count"] == 1
    assert state["retry_count"] == 1
    assert len(llm_service.model.sql_prompts) == 2
    assert executor.executed_sql == [state["generated_sql"]]
    assert "`orders`.`order_no` = `payments`.`order_no`" in state["generated_sql"]
    assert "`orders`.`trace_no` = `payments`.`trace_no`" in state["previous_sql"]
    assert any("JOIN" in prompt and "trace_no" in prompt and "order_no" in prompt for prompt in llm_service.model.sql_prompts[1:])


@pytest.mark.anyio
async def test_agent_graph_runs_six_node_pipeline_and_returns_rows() -> None:
    graph = build_agent_graph(
        StubRagService(),
        StubLLMService(),
        SQLValidator(),
        StubSQLExecutor(),
        catalog=_make_dish_catalog(),
    )

    state = await graph.ainvoke({"user_input": "查询菜品状态和价格", "retry_count": 0, "max_retries": 3})

    assert state["intent"]
    assert state["relevant_tables"]
    assert state["schema_context"]
    assert state["generated_sql"].upper().startswith("SELECT")
    assert state["validation_error"] == ""
    assert state["rows"] == [{"id": 1, "name": "Alice"}]
    assert state["final_answer"]
    assert "sql_generator" in state["debug_trace"]
    assert "sql_plan" not in state["debug_trace"]


@pytest.mark.anyio
async def test_agent_graph_repairs_count_id_before_execution() -> None:
    llm_service = CountRepairLLMService()
    executor = StubSQLExecutor()
    graph = build_agent_graph(
        StubRagService(),
        llm_service,
        SQLValidator(),
        executor,
        catalog=_make_count_catalog(),
    )

    state = await graph.ainvoke({"user_input": "订单数量是多少", "retry_count": 0, "max_retries": 3})

    assert state["status"] == "ready"
    assert state["retry_count"] == 1
    assert len(llm_service.model.sql_prompts) == 2
    assert "COUNT(`order_no`)" in state["generated_sql"]
    assert "COUNT(`id`)" in state["previous_sql"]
    assert any("COUNT(`id`)" in prompt and "COUNT(`order_no`)" in prompt for prompt in llm_service.model.sql_prompts[1:])


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
    assert "ORDER BY" in state["generated_sql"]
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
async def test_agent_graph_sanitizes_schema_catalog_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_agent_graph()

    async def fail_schema_catalog(*args, **kwargs):
        raise RuntimeError("mysql://user:secret@localhost/jc_config is unavailable")

    monkeypatch.setattr("app.services.rag_service._get_schema_catalog", fail_schema_catalog)

    state = await run_agent(
        question="查询员工",
        rag_service=StubRagService(),
        llm_service=StubLLMService(),
        validator=SQLValidator(),
        executor=StubSQLExecutor(),
    )

    assert state["status"] == "error"
    assert state["execution_summary"] == "读取数据库 schema 失败，已停止本次查询。"
    assert "secret" not in state["execution_summary"]
    assert "secret" not in state["explanation"]
    assert state["debug_trace"]["schema_catalog"]["error_class"] == "RuntimeError"


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
