from app.agent.nodes import _build_prompt
from app.agent.nodes import _detect_question_tags
from app.agent.nodes import _load_few_shot_examples
from app.agent.nodes import _load_nl2sql_prompt
from app.agent.nodes import _select_few_shot_examples
from app.agent.nodes import build_fallback_sql


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
    )

    assert "## 6. Reference examples" in prompt
    assert "## 7. Schema context" in prompt
    assert "## 8. User question" in prompt
    assert prompt.index("## 7. Schema context") < prompt.index("## 8. User question")


def test_fallback_sql_sales_has_stable_ordering() -> None:
    sql = build_fallback_sql("近 30 天收入最高的客户是谁？")
    assert "ORDER BY total_revenue DESC, customer_id ASC" in sql
    assert "LIMIT" in sql


def test_fallback_sql_customers_has_stable_ordering() -> None:
    sql = build_fallback_sql("最近注册的用户有哪些？")
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "LIMIT" in sql


def test_fallback_sql_orders_has_stable_ordering() -> None:
    sql = build_fallback_sql("列出最近 30 天的订单")
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "LIMIT" in sql
