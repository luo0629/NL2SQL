from __future__ import annotations

import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from app.agent.state import AgentState
from app.config import get_settings
from app.database.executor import SQLExecutor
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.utils.exceptions import DangerousSQLError
from app.validator.sql_validator import SQLValidator


logger = logging.getLogger(__name__)


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
def _load_few_shot_examples() -> list[dict[str, object]]:
    """加载结构化 few-shot examples。"""
    examples_path = (
        Path(__file__).resolve().parents[1] / "prompts" / "few_shot_examples.json"
    )
    if not examples_path.exists():
        return []

    raw_examples = json.loads(examples_path.read_text(encoding="utf-8"))
    if not isinstance(raw_examples, list):
        return []

    normalized_examples: list[dict[str, object]] = []
    for example in raw_examples:
        if not isinstance(example, dict):
            continue

        question = example.get("question")
        sql = example.get("sql")
        tags = example.get("tags", [])
        if not isinstance(question, str) or not isinstance(sql, str):
            continue

        normalized_tags = [tag.strip().lower() for tag in tags if isinstance(tag, str) and tag.strip()]
        normalized_examples.append(
            {
                "question": question.strip(),
                "sql": sql.strip(),
                "tags": normalized_tags,
            }
        )
    return normalized_examples


def _detect_question_tags(question: str) -> list[str]:
    normalized_question = question.strip().lower()
    tags: list[str] = []

    if any(keyword in normalized_question for keyword in ["sum", "count", "avg", "max", "min", "总", "统计", "汇总", "平均", "收入", "销售额", "金额"]):
        tags.append("aggregation")
    if any(keyword in normalized_question for keyword in ["最近", "近 ", "近", "天", "周", "月", "year", "today", "yesterday", "recent", "latest", "newest"]):
        tags.append("time-range")
    if any(keyword in normalized_question for keyword in ["top", "最高", "最低", "排行", "排名", "前", "best", "worst"]):
        tags.append("top-n")
    if any(keyword in normalized_question for keyword in ["join", "关联", "同时", "以及", "和", "对应"]):
        tags.append("join")
    if not tags:
        tags.append("detail")

    return tags


def query_understanding(state: AgentState) -> AgentState:
    question = state.get("question", "").strip()
    normalized_question = question.lower()
    tags = _detect_question_tags(question)

    intent = "aggregate" if "aggregation" in tags else "select"
    target_mentions: list[str] = []
    condition_mentions: list[dict[str, object]] = []
    value_mentions: list[str] = []

    business_terms = [
        "客户",
        "用户",
        "订单",
        "下单",
        "状态",
        "分类",
        "菜品",
        "口味",
        "价格",
        "销售额",
        "金额",
    ]
    for term in business_terms:
        if term in question:
            target_mentions.append(term)

    condition_markers = ["状态", "分类", "口味", "价格", "金额", "时间"]
    for marker in condition_markers:
        if marker in question:
            condition_mentions.append({"mention": marker})

    quoted_values = re.findall(r"[“\"']([^”\"']+)[”\"']", question)
    value_mentions.extend(quoted_values)
    if "甜" in question and "甜" not in value_mentions:
        value_mentions.append("甜")

    limit: int | None = None
    limit_match = re.search(r"(?:top\s*|前\s*)(\d+)", normalized_question)
    if limit_match:
        limit = int(limit_match.group(1))

    order_by: list[dict[str, object]] = []
    if any(keyword in normalized_question for keyword in ["最高", "最多", "top", "desc"]):
        order_by.append({"direction": "DESC"})
    elif any(keyword in normalized_question for keyword in ["最低", "最少", "asc"]):
        order_by.append({"direction": "ASC"})

    query_understanding_result = {
        "intent": intent,
        "target_mentions": target_mentions,
        "condition_mentions": condition_mentions,
        "value_mentions": value_mentions,
        "aggregation": {"type": "auto"} if "aggregation" in tags else None,
        "group_by": [],
        "order_by": order_by,
        "limit": limit,
        "time_range": {"type": "relative"} if "time-range" in tags else None,
        "requires_join_hint": "join" in tags,
        "tags": tags,
    }

    return {"query_understanding": query_understanding_result}


def _select_few_shot_examples(question: str, limit: int = 3) -> list[dict[str, object]]:
    examples = _load_few_shot_examples()
    if not examples:
        return []

    question_tags = set(_detect_question_tags(question))
    scored_examples: list[tuple[int, int, dict[str, object]]] = []
    fallback_examples: list[dict[str, object]] = []

    for index, example in enumerate(examples):
        example_tags = {
            tag for tag in cast(list[str], example.get("tags", []))
        }
        overlap = len(question_tags & example_tags)
        if overlap > 0:
            scored_examples.append((overlap, index, example))
        elif "detail" in example_tags:
            fallback_examples.append(example)

    selected = [
        example
        for _score, _index, example in sorted(
            scored_examples,
            key=lambda item: (-item[0], item[1]),
        )[:limit]
    ]
    if selected:
        return selected
    return fallback_examples[:limit]


def _format_few_shot_examples(examples: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for index, example in enumerate(examples, 1):
        question = cast(str, example["question"])
        sql = cast(str, example["sql"])
        tags = cast(list[str], example.get("tags", []))
        tag_line = f"Tags: {', '.join(tags)}\n" if tags else ""
        parts.append(f"Example {index}:\n{tag_line}Question: {question}\nSQL:\n{sql}")
    return "\n\n".join(parts)


def _build_prompt(
    question: str,
    schema_context: list[str],
    business_semantic_brief: dict[str, Any] | None = None,
    join_path_plan: dict[str, Any] | None = None,
    schema_linking: dict[str, Any] | None = None,
) -> str:
    joined_schema = "\n".join(f"- {item}" for item in schema_context)
    prompt = _load_nl2sql_prompt()
    selected_examples = _select_few_shot_examples(question)
    few_shot = _format_few_shot_examples(selected_examples)

    parts: list[str] = [prompt]

    if few_shot:
        parts.append(f"## 6. Reference examples\nUse the following examples only as style and structure references.\n\n{few_shot}")

    if business_semantic_brief:
        prompt_block = cast(str, business_semantic_brief.get("prompt_block", "")).strip()
        if prompt_block:
            parts.append(prompt_block)

    if schema_linking:
        linking_summary = cast(str, schema_linking.get("linking_summary", "")).strip()
        matched_tables = cast(list[dict[str, Any]], schema_linking.get("matched_tables", []))
        table_summary = ", ".join(
            cast(str, table.get("table_name", ""))
            for table in matched_tables[:6]
            if table.get("table_name")
        )
        lines = ["## Schema linking plan"]
        if linking_summary:
            lines.append(f"Summary: {linking_summary}")
        if table_summary:
            lines.append(f"Matched tables: {table_summary}")
        parts.append("\n".join(lines))

    if join_path_plan:
        planning_summary = cast(str, join_path_plan.get("planning_summary", "")).strip()
        plan_confidence = cast(str, join_path_plan.get("plan_confidence", "")).strip()
        lines = ["## Join path plan"]
        if plan_confidence:
            lines.append(f"Confidence: {plan_confidence}")
        if planning_summary:
            lines.append(f"Summary: {planning_summary}")
        parts.append("\n".join(lines))

    parts.append(f"## 7. Schema context\nUse this as the only source of truth.\n{joined_schema}")
    parts.append(f"## 8. User question\n{question}")
    parts.append("## 9. Final reminder\nReturn exactly one SQL statement ending with a semicolon.")

    return "\n\n".join(parts)


async def retrieve_schema(state: AgentState, rag_service: RagService) -> AgentState:
    # 从状态中取问题，补充与问题相关的 schema 上下文和结构化 schema plan。
    question = state.get("question", "")
    query_schema_plan = (await rag_service.build_query_schema_plan(question)).model_dump()
    return {
        "query_schema_plan": query_schema_plan,
        "schema_context": cast(list[str], query_schema_plan.get("schema_context", [])),
    }


def schema_linking(state: AgentState) -> AgentState:
    # 从 query schema plan 中取出结构化 schema linking 结果并铺平到 state。
    query_schema_plan = state.get("query_schema_plan", {})
    schema_linking_result = cast(dict[str, Any], query_schema_plan.get("schema_linking", {}))
    return {
        "schema_linking": schema_linking_result,
        "linking_summary": cast(str, schema_linking_result.get("linking_summary", "")),
    }


def value_linking(state: AgentState) -> AgentState:
    schema_linking_result = state.get("schema_linking", {})
    query_understanding_result = state.get("query_understanding", {})
    value_mentions = cast(list[str], query_understanding_result.get("value_mentions", []))
    linked_tables = schema_linking_result.get("linked_tables", [])
    primary_table = linked_tables[0].get("name") if linked_tables else None

    value_links = [
        {
            "mention": mention,
            "field_mention": None,
            "table": primary_table,
            "column": None,
            "db_value": mention,
            "confidence": 0.5,
            "match_type": "typed_literal",
            "source": "literal",
        }
        for mention in value_mentions
    ]

    return {"value_links": value_links}


def join_path_planning(state: AgentState) -> AgentState:
    # 从 query schema plan 中取出 join path planning 结果并铺平到 state。
    query_schema_plan = state.get("query_schema_plan", {})
    join_path_plan_result = cast(dict[str, Any], query_schema_plan.get("join_path_plan", {}))
    return {
        "join_path_plan": join_path_plan_result,
        "join_planning_summary": cast(str, join_path_plan_result.get("planning_summary", "")),
    }


def build_semantic_brief(state: AgentState) -> AgentState:
    # 从 query schema plan 中取出业务语义说明并铺平到 state。
    query_schema_plan = state.get("query_schema_plan", {})
    business_semantic_brief_result = cast(
        dict[str, Any],
        query_schema_plan.get("business_semantic_brief", {}),
    )
    return {"business_semantic_brief": business_semantic_brief_result}


def sql_planning(state: AgentState) -> AgentState:
    schema_linking_result = state.get("schema_linking", {})
    join_path_plan = state.get("join_path_plan", {})
    query_understanding_result = state.get("query_understanding", {})
    linked_tables = schema_linking_result.get("linked_tables", [])
    from_table = linked_tables[0].get("name") if linked_tables else None

    sql_plan = {
        "select": [],
        "from_table": from_table,
        "joins": join_path_plan.get("join_edges", []),
        "where": [],
        "group_by": [],
        "having": [],
        "order_by": query_understanding_result.get("order_by", []),
        "limit": query_understanding_result.get("limit"),
        "distinct": bool(join_path_plan.get("requires_distinct", False)),
        "params": [],
        "provenance": {
            "schema_linking": bool(schema_linking_result),
            "value_linking": bool(state.get("value_links", [])),
            "join_path_planning": bool(join_path_plan),
        },
    }

    return {"sql_plan": sql_plan, "sql_params": sql_plan["params"]}


def sql_repairing(state: AgentState) -> AgentState:
    retry_count = state.get("retry_count", 0) + 1
    repair_attempts = state.get("repair_attempts", 0) + 1
    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["last_repair"] = {
        "attempt": repair_attempts,
        "validation_errors": state.get("validation_errors", []),
        "validation_issues": state.get("validation_issues", []),
    }

    return {
        "retry_count": retry_count,
        "repair_attempts": repair_attempts,
        "debug_trace": debug_trace,
    }


def generate_sql(state: AgentState, llm_service: LLMService) -> AgentState:
    # 先尝试真实模型生成；不可用时自动回退到教学型 SQL。
    question = state.get("question", "")
    schema_context = state.get("schema_context", [])
    business_semantic_brief = cast(dict[str, Any], state.get("business_semantic_brief", {}))
    join_path_plan = cast(dict[str, Any], state.get("join_path_plan", {}))
    schema_linking_result = cast(dict[str, Any], state.get("schema_linking", {}))
    question_tags = _detect_question_tags(question)
    selected_examples = _select_few_shot_examples(question)
    prompt = _build_prompt(
        question,
        schema_context,
        business_semantic_brief=business_semantic_brief,
        join_path_plan=join_path_plan,
        schema_linking=schema_linking_result,
    )
    model = llm_service.build_chat_model()

    logger.info(
        "llm_generation_start tags=%s schema_items=%s few_shots=%s prompt_chars=%s",
        question_tags,
        len(schema_context),
        len(selected_examples),
        len(prompt),
    )

    if model is None:
        logger.info(
            "llm_generation_fallback_model_unavailable tags=%s schema_items=%s few_shots=%s prompt_chars=%s",
            question_tags,
            len(schema_context),
            len(selected_examples),
            len(prompt),
        )
        return {
            "sql": build_fallback_sql(question),
            "status": "mock",
            "used_fallback": True,
            "explanation": (
                "当前使用的是教学型 fallback 模式：系统根据问题关键词和内置 schema 摘要生成了一条稳定的示例 SQL。"
            ),
        }

    try:
        response = model.invoke(prompt)
        content = _extract_text(
            cast(str | list[str | dict[str, str]], response.content)
        )
        logger.info(
            "llm_generation_success tags=%s schema_items=%s few_shots=%s prompt_chars=%s sql_chars=%s",
            question_tags,
            len(schema_context),
            len(selected_examples),
            len(prompt),
            len(content),
        )
        return {
            "sql": _normalize_sql(content),
            "status": "ready",
            "used_fallback": False,
            "explanation": "已调用 Zhipu GLM 生成 SQL，接下来会进入只读安全校验。",
        }
    except Exception as error:
        logger.warning(
            "llm_generation_fallback_provider_error tags=%s schema_items=%s few_shots=%s prompt_chars=%s error_type=%s error=%s",
            question_tags,
            len(schema_context),
            len(selected_examples),
            len(prompt),
            error.__class__.__name__,
            error,
        )
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
    linking_summary = state.get("linking_summary", "")
    join_planning_summary = state.get("join_planning_summary", "")
    validation_errors = state.get("validation_errors", [])
    validation_issues = state.get("validation_issues", [])
    execution_time_ms = state.get("execution_time_ms")

    if schema_context:
        explanation = (
            f"{explanation} 当前参考的 schema 摘要数量：{len(schema_context)}。"
        )

    if linking_summary:
        explanation = f"{explanation} Schema linking：{linking_summary}"

    if join_planning_summary:
        explanation = f"{explanation} Join planning：{join_planning_summary}"

    if validation_errors:
        explanation = f"{explanation} 最近一次校验问题：{validation_errors[0]}"

    if execution_time_ms is not None:
        explanation = f"{explanation} 执行耗时：{execution_time_ms:.0f}ms。"

    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace.update(
        {
            "query_understanding": state.get("query_understanding", {}),
            "schema_links": state.get("schema_linking", {}),
            "value_links": state.get("value_links", []),
            "join_paths": state.get("join_path_plan", {}),
            "sql_plan": state.get("sql_plan", {}),
            "validation_errors": validation_errors,
            "validation_issues": validation_issues,
            "fallback": {"used": state.get("used_fallback", False)},
            "execution": {
                "row_count": state.get("row_count", 0),
                "truncated": state.get("truncated", False),
                "execution_time_ms": execution_time_ms,
            },
            "schema_context_count": len(schema_context),
            "confidence": state.get("join_path_plan", {}).get("confidence", 0.0),
        }
    )

    return {"explanation": explanation, "debug_trace": debug_trace}
