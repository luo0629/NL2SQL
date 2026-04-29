from app.agent.nodes import build_fallback_sql
from app.agent.nodes import _load_nl2sql_prompt


def test_nl2sql_prompt_contains_stability_rules() -> None:
    prompt = _load_nl2sql_prompt()
    assert "LIMIT without ORDER BY is not allowed" in prompt


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

