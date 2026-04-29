from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import cast

from app.agent.state import AgentState
from app.config import get_settings
from app.database.executor import SQLExecutor
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.utils.exceptions import DangerousSQLError
from app.validator.sql_validator import SQLValidator


def _infer_primary_table(question: str) -> str:
    # 极简关键词路由：用于 fallback 模式下决定优先查询哪张表。
    normalized_question = question.strip().lower()

    if any(
        keyword in normalized_question
        for keyword in ["sales", "revenue", "收入", "销售"]
    ):
        return "sales"

    if any(
        keyword in normalized_question
        for keyword in ["customer", "user", "客户", "用户"]
    ):
        return "customers"

    return "orders"


def build_fallback_sql(question: str) -> str:
    # 当模型不可用或生成失败时，返回可稳定演示的只读 SQL 模板。
    table_name = _infer_primary_table(question)
    driver_name = (get_settings().database_url or "").lower()

    if "mysql" in driver_name:
        recent_filter = "created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"
    elif "sqlite" in driver_name:
        recent_filter = "created_at >= date('now', '-30 day')"
    else:
        # Default: Postgres-like interval syntax.
        recent_filter = "created_at >= CURRENT_DATE - INTERVAL '30 days'"

    if table_name == "sales":
        return (
            "SELECT customer_id, SUM(amount) AS total_revenue\n"
            "FROM sales\n"
            f"WHERE {recent_filter}\n"
            "GROUP BY customer_id\n"
            "ORDER BY total_revenue DESC, customer_id ASC\n"
            "LIMIT 10;"
        )

    if table_name == "customers":
        return (
            "SELECT id, name, segment, created_at\n"
            "FROM customers\n"
            f"WHERE {recent_filter}\n"
            "ORDER BY created_at DESC, id DESC\n"
            "LIMIT 20;"
        )

    return (
        "SELECT id, customer_id, total_amount, status, created_at\n"
        "FROM orders\n"
        f"WHERE {recent_filter}\n"
        "ORDER BY created_at DESC, id DESC\n"
        "LIMIT 20;"
    )


def _extract_text(content: str | list[str | dict[str, str]]) -> str:
    # 兼容不同模型响应结构：统一提取为纯文本 SQL。
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue

        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)

    return "\n".join(parts)


def _normalize_sql(candidate: str) -> str:
    # 清理 markdown 包裹并确保语句以分号结尾，便于后续校验。
    cleaned = candidate.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].lstrip()

    if not cleaned.endswith(";"):
        cleaned = f"{cleaned};"

    return cleaned


@lru_cache(maxsize=1)
def _load_nl2sql_prompt() -> str:
    prompt_path = (
        Path(__file__).resolve().parents[1] / "prompts" / "nl2sql_prompt.txt"
    )
    return prompt_path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _load_few_shot_examples() -> str:
    """加载 few-shot examples 并格式化为 prompt 片段。"""
    examples_path = (
        Path(__file__).resolve().parents[1] / "prompts" / "few_shot_examples.json"
    )
    if not examples_path.exists():
        return ""

    examples = json.loads(examples_path.read_text(encoding="utf-8"))
    if not examples:
        return ""

    parts: list[str] = []
    for i, example in enumerate(examples, 1):
        q = example.get("question", "")
        s = example.get("sql", "")
        if q and s:
            parts.append(f"Example {i}:\nQuestion: {q}\nSQL:\n{s}")
    return "\n\n".join(parts)


def _build_prompt(question: str, schema_context: list[str]) -> str:
    joined_schema = "\n".join(f"- {item}" for item in schema_context)
    prompt = _load_nl2sql_prompt()
    few_shot = _load_few_shot_examples()

    # 基础 prompt + few-shot examples + schema context + 用户问题
    parts: list[str] = [prompt]

    if few_shot:
        parts.append(f"Reference Examples (follow the same SQL style and patterns):\n{few_shot}")

    parts.append(f"Question:\n{question}")
    parts.append(f"Schema Context (use as the only source of truth):\n{joined_schema}")

    return "\n\n".join(parts)


async def retrieve_schema(state: AgentState, rag_service: RagService) -> AgentState:
    # 从状态中取问题，补充与问题相关的 schema 上下文。
    question = state.get("question", "")
    return {"schema_context": await rag_service.retrieve_relevant_schema(question)}


def generate_sql(state: AgentState, llm_service: LLMService) -> AgentState:
    # 先尝试真实模型生成；不可用时自动回退到教学型 SQL。
    question = state.get("question", "")
    schema_context = state.get("schema_context", [])
    model = llm_service.build_chat_model()

    if model is None:
        return {
            "sql": build_fallback_sql(question),
            "status": "mock",
            "used_fallback": True,
            "explanation": (
                "当前使用的是教学型 fallback 模式：系统根据问题关键词和内置 schema 摘要生成了一条稳定的示例 SQL。"
            ),
        }

    # TODO(learning): 当前 prompt 仍然非常简化，后续可以把 few-shot、业务术语和 schema RAG 拼装进来。
    try:
        response = model.invoke(_build_prompt(question, schema_context))
        content = _extract_text(
            cast(str | list[str | dict[str, str]], response.content)
        )
        return {
            "sql": _normalize_sql(content),
            "status": "ready",
            "used_fallback": False,
            "explanation": "已调用 Zhipu GLM 生成 SQL，接下来会进入只读安全校验。",
        }
    except Exception:
        return {
            "sql": build_fallback_sql(question),
            "status": "mock",
            "used_fallback": True,
            "explanation": "真实模型调用失败，系统已自动回退到稳定的教学型示例 SQL。",
        }


def validate_sql(state: AgentState, validator: SQLValidator) -> AgentState:
    # 只允许只读 SQL，校验失败则标记重试计数，由 Graph 条件路由决定是否重新生成。
    sql = state.get("sql", "")
    question = state.get("question", "")
    retry_count = state.get("retry_count", 0)

    try:
        validator.validate_read_only(sql)
        return {}
    except DangerousSQLError as error:
        if retry_count < 1:
            # 第一次验证失败：标记重试，由条件路由回到 generate_sql
            return {
                "validation_errors": [str(error)],
                "retry_count": retry_count + 1,
                "explanation": f"SQL 校验未通过（{error}），正在尝试重新生成...",
            }
        else:
            # 重试仍失败：回退到 fallback SQL
            return {
                "sql": build_fallback_sql(question),
                "status": "mock",
                "used_fallback": True,
                "validation_errors": [str(error)],
                "explanation": (
                    "重新生成后仍未通过校验，系统已自动回退到稳定的示例 SQL。"
                ),
            }


async def execute_sql(state: AgentState, executor: SQLExecutor) -> AgentState:
    # 在 Graph 内执行 SQL，填充结果字段。
    sql = state.get("sql", "")
    started_at = time.monotonic()

    try:
        result = await executor.execute(sql)
        elapsed_ms = (time.monotonic() - started_at) * 1000
        return {
            "rows": result.rows,
            "columns": result.columns,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_summary": result.execution_summary,
        }
    except Exception as error:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        return {
            "status": "error",
            "rows": [],
            "columns": [],
            "row_count": 0,
            "truncated": False,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_summary": f"执行失败：{error}",
            "explanation": f"SQL 执行出错：{error}",
        }


def finalize_response(state: AgentState) -> AgentState:
    # 汇总说明文本，附加 schema 使用情况与最近一次校验信息。
    explanation = state.get("explanation", "当前返回的是教学型 SQL 结果。")
    schema_context = state.get("schema_context", [])
    validation_errors = state.get("validation_errors", [])
    execution_time_ms = state.get("execution_time_ms")

    if schema_context:
        explanation = (
            f"{explanation} 当前参考的 schema 摘要数量：{len(schema_context)}。"
        )

    if validation_errors:
        explanation = f"{explanation} 最近一次校验问题：{validation_errors[0]}"

    if execution_time_ms is not None:
        explanation = f"{explanation} 执行耗时：{execution_time_ms:.0f}ms。"

    return {"explanation": explanation}
