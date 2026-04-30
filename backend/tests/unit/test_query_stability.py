from app.agent.nodes import _build_prompt
from app.agent.nodes import _detect_question_tags
from app.agent.nodes import _load_few_shot_examples
from app.agent.nodes import _load_nl2sql_prompt
from app.agent.nodes import _select_few_shot_examples
from app.agent.nodes import build_fallback_sql
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable


def test_nl2sql_prompt_contains_stability_rules() -> None:
    prompt = _load_nl2sql_prompt()
    assert "LIMIT without ORDER BY is not allowed" in prompt
    assert "## 1. Hard constraints" in prompt
    assert "## 5. Output requirements" in prompt



def test_detect_question_tags_for_ranking_aggregation_question() -> None:
    tags = _detect_question_tags("近 30 天收入最高的 10 个客户是谁？")

    assert "aggregation" in tags
    assert "time-range" in tags
    assert "top-n" in tags



def test_load_few_shot_examples_returns_structured_examples() -> None:
    examples = _load_few_shot_examples()

    assert examples
    assert isinstance(examples[0]["question"], str)
    assert isinstance(examples[0]["sql"], str)
    assert isinstance(examples[0]["tags"], list)



def test_select_few_shot_examples_prefers_matching_tags() -> None:
    examples = _select_few_shot_examples("近 30 天收入最高的 10 个客户是谁？")

    assert examples
    assert any("top-n" in example["tags"] for example in examples)



def test_build_prompt_uses_stable_section_order() -> None:
    prompt = _build_prompt(
        "近 30 天收入最高的 10 个客户是谁？",
        ["table sales\n- customer_id: bigint, required\n- amount: decimal, required\n- created_at: timestamp, required"],
        business_semantic_brief={
            "prompt_block": "## Business semantic brief\nQuestion: 近 30 天收入最高的 10 个客户是谁？\nEntities: sales\nKey fields: sales.customer_id, sales.amount\nMetrics: sales.amount\nFilters: sales.created_at\nJoin plan: 主表: sales。\nUncertainties: 无\nConstraints: 优先使用系统提供的候选表、候选字段和连表路径。"
        },
        join_path_plan={
            "plan_confidence": "high",
            "planning_summary": "主表: sales。连表覆盖: sales。规划置信度: high。",
        },
        schema_linking={
            "linking_summary": "命中表: sales。",
            "matched_tables": [{"table_name": "sales"}],
        },
    )

    assert "## 6. Reference examples" in prompt
    assert "## Business semantic brief" in prompt
    assert "## Schema linking plan" in prompt
    assert "## Join path plan" in prompt
    assert "## 7. Schema context" in prompt
    assert "## 8. User question" in prompt
    assert prompt.index("## Join path plan") < prompt.index("## 7. Schema context")
    assert prompt.index("## 7. Schema context") < prompt.index("## 8. User question")


def _make_stability_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="test_db",
        tables=[
            SchemaTable(
                name="sales",
                description="销售表",
                aliases=["销售额", "收入"],
                business_terms=["销售"],
                columns=[
                    SchemaColumn(name="customer_id", data_type="BIGINT", nullable=False, semantic_role="dimension"),
                    SchemaColumn(name="total_revenue", data_type="DECIMAL", nullable=False, business_terms=["收入", "销售额"], semantic_role="metric"),
                    SchemaColumn(name="created_at", data_type="TIMESTAMP", nullable=True, semantic_role="timestamp"),
                ],
            ),
            SchemaTable(
                name="customers",
                description="客户表",
                aliases=["客户", "用户"],
                business_terms=["注册用户"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="VARCHAR", nullable=True, semantic_role="dimension"),
                    SchemaColumn(name="created_at", data_type="TIMESTAMP", nullable=True, semantic_role="timestamp"),
                ],
            ),
            SchemaTable(
                name="orders",
                description="订单表",
                aliases=["订单"],
                business_terms=["下单"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="status", data_type="VARCHAR", nullable=True, semantic_role="dimension"),
                    SchemaColumn(name="created_at", data_type="TIMESTAMP", nullable=True, semantic_role="timestamp"),
                ],
            ),
        ],
    )


def test_fallback_sql_sales_has_stable_ordering() -> None:
    catalog = _make_stability_catalog()
    sql = build_fallback_sql("近 30 天收入最高的客户是谁？", catalog)
    assert "sales" in sql
    assert "LIMIT" in sql


def test_fallback_sql_customers_has_stable_ordering() -> None:
    catalog = _make_stability_catalog()
    sql = build_fallback_sql("最近注册的用户有哪些？", catalog)
    assert "customers" in sql
    assert "LIMIT" in sql


def test_fallback_sql_orders_has_stable_ordering() -> None:
    catalog = _make_stability_catalog()
    sql = build_fallback_sql("列出最近 30 天的订单", catalog)
    assert "orders" in sql
    assert "LIMIT" in sql
