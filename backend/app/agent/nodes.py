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
from app.rag.schema_models import SchemaCatalog
from app.rag.sql_generator import SQLGenerator
from app.rag.sql_planner import SQLPlanner
from app.rag.sql_repairer import SQLRepairer
from app.rag.value_linker import ValueLinker
from app.services.rag_service import RagService
from app.utils.exceptions import DangerousSQLError
from app.validator.sql_validator import SQLValidator


logger = logging.getLogger(__name__)


def _infer_primary_table(question: str, catalog: SchemaCatalog | None = None) -> str | None:
    """从 schema catalog 中动态匹配最相关的表。无 catalog 时返回 None。"""
    if not catalog or not catalog.tables:
        return None

    normalized_question = question.strip().lower()
    if not normalized_question:
        return catalog.tables[0].name if catalog.tables else None

    best_table = None
    best_score = 0

    for table in catalog.tables:
        score = 0
        table_name_lower = table.name.lower()
        description_lower = (table.description or "").lower()
        alias_terms = {alias.lower().strip() for alias in table.aliases if alias.strip()}
        business_terms = {term.lower().strip() for term in table.business_terms if term.strip()}

        # 表名直接匹配
        if table_name_lower in normalized_question:
            score += 10

        # 描述匹配
        if description_lower and description_lower in normalized_question:
            score += 6

        # 别名匹配
        for alias in alias_terms:
            if alias and alias in normalized_question:
                score += 5

        # 业务术语匹配
        for term in business_terms:
            if term and term in normalized_question:
                score += 5

        # 列级匹配加分
        for column in table.columns:
            col_desc_lower = (column.description or "").lower()
            for bt in column.business_terms:
                if bt.lower().strip() and bt.lower().strip() in normalized_question:
                    score += 2
            if col_desc_lower and col_desc_lower in normalized_question:
                score += 2

        if score > best_score:
            best_score = score
            best_table = table.name

    return best_table


def _find_time_column(table) -> str | None:
    """找到表中适合做时间过滤的列。"""
    # 优先找 semantic_role 为 timestamp 的列
    for column in table.columns:
        if column.semantic_role == "timestamp":
            return column.name
    # 次优：列名包含常见时间关键词
    time_keywords = ["time", "date", "created", "updated", "at"]
    for column in table.columns:
        if any(kw in column.name.lower() for kw in time_keywords):
            return column.name
    return None


def _find_order_column(table) -> str | None:
    """找到表中适合做排序的列（优先时间列，次选数值列）。"""
    # 优先时间列
    time_col = _find_time_column(table)
    if time_col:
        return time_col
    # 次选：id 列
    for column in table.columns:
        if column.is_primary_key:
            return column.name
    return None


def build_fallback_sql(question: str, catalog: SchemaCatalog | None = None) -> str:
    """当模型不可用或生成失败时，基于当前 schema 动态生成安全的只读 SQL。"""
    # 无 catalog 时返回安全降级 SQL
    if not catalog or not catalog.tables:
        return "SELECT 1 AS result;"

    table_name = _infer_primary_table(question, catalog)
    if not table_name:
        # 找不到匹配表，使用第一张表
        table_name = catalog.tables[0].name

    # 查找对应的表元数据
    target_table = None
    for table in catalog.tables:
        if table.name == table_name:
            target_table = table
            break

    if not target_table:
        return "SELECT 1 AS result;"

    # 选择展示列（最多 5 个非主键列）
    display_columns = [
        col.name for col in target_table.columns
        if not col.is_primary_key
    ][:5]
    # 如果没有非主键列，用主键列
    if not display_columns:
        display_columns = [col.name for col in target_table.columns if col.is_primary_key][:1]
    if not display_columns:
        display_columns = ["*"]

    col_list = ", ".join(display_columns)
    order_col = _find_order_column(target_table)

    # 构建 SQL
    parts = [f"SELECT {col_list} FROM {table_name}"]

    # 尝试加时间过滤
    time_col = _find_time_column(target_table)
    if time_col:
        driver_name = (get_settings().database_url or "").lower()
        if "mysql" in driver_name:
            parts.append(f"WHERE {time_col} >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)")
        elif "sqlite" in driver_name:
            parts.append(f"WHERE {time_col} >= date('now', '-30 day')")
        else:
            parts.append(f"WHERE {time_col} >= CURRENT_DATE - INTERVAL '30 days'")

    if order_col:
        parts.append(f"ORDER BY {order_col} DESC")

    parts.append("LIMIT 20")

    return "\n".join(parts) + ";"


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


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return payload if isinstance(payload, dict) else None


def _invoke_model_json(model: Any, prompt: str) -> dict[str, Any] | None:
    try:
        response = model.invoke(prompt)
        content = _extract_text(cast(str | list[str | dict[str, str]], response.content))
    except Exception:
        return None
    return _extract_json_object(content)


def _extract_catalog_business_terms(catalog: SchemaCatalog | None) -> tuple[list[str], list[str]]:
    """从 schema catalog 动态提取业务术语和条件标记。"""
    if not catalog or not catalog.tables:
        return [], []

    business_terms: list[str] = []
    condition_markers: list[str] = []

    for table in catalog.tables:
        if table.description:
            business_terms.append(table.description)
        for alias in table.aliases:
            if alias.strip():
                business_terms.append(alias.strip())
        for term in table.business_terms:
            if term.strip():
                business_terms.append(term.strip())

        for column in table.columns:
            if column.description:
                business_terms.append(column.description)
            for term in column.business_terms:
                if term.strip():
                    business_terms.append(term.strip())
            if column.semantic_role in ("dimension", "foreign_key", "timestamp"):
                if column.description:
                    condition_markers.append(column.description)
                for term in column.business_terms:
                    if term.strip():
                        condition_markers.append(term.strip())

    business_terms = list(dict.fromkeys(business_terms))
    condition_markers = list(dict.fromkeys(condition_markers))
    return business_terms, condition_markers


def _fallback_query_understanding(question: str, catalog: SchemaCatalog | None = None) -> dict[str, Any]:
    normalized_question = question.lower()
    tags = _detect_question_tags(question)

    metric_rules = [
        ("COUNT", ["多少", "数量", "个数", "条数", "几个", "几条", "count"]),
        ("SUM", ["总额", "合计", "销售额", "收入", "金额", "销量", "sum", "total"]),
        ("AVG", ["平均", "均值", "avg"]),
        ("MAX", ["最大", "最高", "最多", "max"]),
        ("MIN", ["最小", "最低", "最少", "min"]),
    ]
    dimension_terms = ["门店", "店铺", "分类", "类别", "客户", "用户", "商品", "菜品", "套餐", "地区", "城市", "渠道", "日期", "月份", "年份"]
    status_terms = ["起售", "停售", "在售", "上架", "下架", "启用", "禁用", "有效", "无效", "已支付", "未支付", "已完成", "已取消"]

    metrics: list[dict[str, object]] = []
    for aggregation_type, keywords in metric_rules:
        for keyword in keywords:
            if keyword in normalized_question:
                metrics.append({"term": keyword, "aggregation": aggregation_type})
                break

    aggregation = {"type": metrics[0]["aggregation"], "metrics": metrics} if metrics else ({"type": "auto", "metrics": []} if "aggregation" in tags else None)
    intent = "ranking" if "top-n" in tags else ("aggregate" if aggregation else "select")

    target_mentions: list[str] = []
    condition_mentions: list[dict[str, object]] = []
    value_mentions: list[str] = []
    value_terms: list[str] = []
    dimensions: list[str] = []
    filters: list[dict[str, object]] = []

    catalog_business_terms, catalog_condition_markers = _extract_catalog_business_terms(catalog)
    if not catalog_business_terms:
        catalog_business_terms = ["客户", "用户", "订单", "商品", "菜品", "状态", "分类", "价格", "金额", "时间"]
    if not catalog_condition_markers:
        catalog_condition_markers = ["状态", "分类", "价格", "金额", "时间"]

    for term in catalog_business_terms:
        if term in question and term not in target_mentions:
            target_mentions.append(term)

    for marker in catalog_condition_markers:
        if marker in question:
            condition_mentions.append({"mention": marker})
            filters.append({"term": marker, "operator": None, "value": None})

    for term in status_terms:
        if term in question:
            value_terms.append(term)
            value_mentions.append(term)
            if not any(item.get("mention") == "状态" for item in condition_mentions):
                condition_mentions.append({"mention": "状态"})
            filters.append({"term": "状态", "operator": "=", "value": term})

    for dimension in dimension_terms:
        if re.search(rf"(?:各|每个?|按|分)\s*{re.escape(dimension)}", question) or f"{dimension}维度" in question:
            dimensions.append(dimension)

    quoted_values = re.findall(r"[“”‘’\"']([^“”‘’\"']+)[“”‘’\"']", question)
    value_mentions.extend(value for value in quoted_values if value not in value_mentions)

    limit: int | None = None
    limit_match = re.search(r"(?:top\s*|前\s*)(\d+)", normalized_question)
    if limit_match:
        limit = int(limit_match.group(1))
    else:
        chinese_limit_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        chinese_limit_match = re.search(r"前\s*([一二两三四五六七八九十])", question)
        if chinese_limit_match:
            limit = chinese_limit_map[chinese_limit_match.group(1)]

    order_by: list[dict[str, object]] = []
    sort: dict[str, object] | None = None
    if any(keyword in normalized_question for keyword in ["最高", "最多", "top", "desc", "最贵", "最热门", "最好", "最受欢迎", "最畅销"]):
        sort = {"term": metrics[0]["term"] if metrics else None, "direction": "DESC"}
        order_by.append({"term": sort["term"], "direction": "DESC"})
    elif any(keyword in normalized_question for keyword in ["最低", "最少", "asc", "最便宜", "最差"]):
        sort = {"term": metrics[0]["term"] if metrics else None, "direction": "ASC"}
        order_by.append({"term": sort["term"], "direction": "ASC"})

    time_range: dict[str, object] | None = None
    relative_time_match = re.search(r"(?:最近|近)\s*(\d+)\s*(天|日|周|月|年)", question)
    if relative_time_match:
        time_range = {"type": "relative", "amount": int(relative_time_match.group(1)), "unit": relative_time_match.group(2)}
    elif "最近一个月" in question or "近一个月" in question:
        time_range = {"type": "relative", "amount": 1, "unit": "月"}
    elif "time-range" in tags:
        time_range = {"type": "relative"}

    return {
        "intent": intent,
        "target_mentions": target_mentions,
        "condition_mentions": condition_mentions,
        "value_mentions": value_mentions,
        "value_terms": value_terms,
        "metrics": metrics,
        "dimensions": dimensions,
        "filters": filters,
        "aggregation": aggregation,
        "group_by": [{"term": dimension} for dimension in dimensions],
        "order_by": order_by,
        "sort": sort,
        "limit": limit,
        "time_range": time_range,
        "requires_join_hint": "join" in tags,
        "tags": tags,
        "source": "deterministic",
    }


def _normalize_query_understanding_payload(
    question: str,
    payload: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    result = dict(fallback)
    result["intent"] = str(payload.get("intent") or fallback.get("intent") or "select")
    result["target_mentions"] = [str(item) for item in payload.get("target_mentions", fallback.get("target_mentions", [])) if str(item).strip()]
    result["value_mentions"] = [str(item) for item in payload.get("value_mentions", fallback.get("value_mentions", [])) if str(item).strip()]

    raw_conditions = payload.get("condition_mentions", fallback.get("condition_mentions", []))
    normalized_conditions: list[dict[str, object]] = []
    if isinstance(raw_conditions, list):
        for item in raw_conditions:
            if isinstance(item, dict):
                mention = item.get("mention")
                if mention:
                    normalized_conditions.append({"mention": str(mention)})
            elif item:
                normalized_conditions.append({"mention": str(item)})
    result["condition_mentions"] = normalized_conditions

    raw_order = payload.get("order_by", fallback.get("order_by", []))
    order_by: list[dict[str, object]] = []
    if isinstance(raw_order, list):
        for item in raw_order:
            if isinstance(item, dict):
                normalized_item: dict[str, object] = {}
                if item.get("table"):
                    normalized_item["table"] = str(item["table"])
                if item.get("column"):
                    normalized_item["column"] = str(item["column"])
                if item.get("term"):
                    normalized_item["term"] = str(item["term"])
                direction = str(item.get("direction") or "ASC").upper()
                normalized_item["direction"] = "DESC" if direction == "DESC" else "ASC"
                order_by.append(normalized_item)
    result["order_by"] = order_by or cast(list[dict[str, object]], fallback.get("order_by", []))

    limit = payload.get("limit")
    result["limit"] = limit if isinstance(limit, int) and limit > 0 else fallback.get("limit")
    result["group_by"] = payload.get("group_by", fallback.get("group_by", [])) if isinstance(payload.get("group_by", fallback.get("group_by", [])), list) else []
    result["aggregation"] = payload.get("aggregation", fallback.get("aggregation"))
    result["time_range"] = payload.get("time_range", fallback.get("time_range"))
    result["requires_join_hint"] = bool(payload.get("requires_join_hint", fallback.get("requires_join_hint", False)))
    result["ambiguities"] = payload.get("ambiguities", []) if isinstance(payload.get("ambiguities", []), list) else []
    for key in ["value_terms", "metrics", "dimensions", "filters"]:
        value = payload.get(key, fallback.get(key, []))
        result[key] = value if isinstance(value, list) else []
    sort = payload.get("sort", fallback.get("sort"))
    result["sort"] = sort if isinstance(sort, dict) else None
    result["question"] = question
    result["source"] = "llm"
    result["tags"] = fallback.get("tags", [])
    return result


def _normalize_sql_plan_candidate(
    candidate: dict[str, Any],
    fallback_plan: dict[str, Any],
    schema_linking: dict[str, Any],
    value_links: list[dict[str, Any]],
) -> dict[str, Any]:
    matched_tables = cast(list[dict[str, Any]], schema_linking.get("matched_tables", schema_linking.get("linked_tables", [])))
    allowed_columns: dict[str, set[str]] = {}
    for table in matched_tables:
        table_name = str(table.get("table_name") or table.get("name") or "").strip()
        if not table_name:
            continue
        allowed_columns.setdefault(table_name, set())
        for column in table.get("matched_columns", []):
            column_name = str(column.get("column_name") or column.get("name") or "").strip()
            if column_name:
                allowed_columns[table_name].add(column_name)

    from_table = str(candidate.get("from_table") or fallback_plan.get("from_table") or "").strip() or None
    if from_table not in allowed_columns and fallback_plan.get("from_table"):
        from_table = cast(str, fallback_plan.get("from_table"))

    select_items: list[dict[str, object]] = []
    raw_select = candidate.get("select", [])
    if isinstance(raw_select, list):
        for item in raw_select:
            if not isinstance(item, dict):
                continue
            table = str(item.get("table") or from_table or "").strip()
            column = str(item.get("column") or "").strip()
            if not table or not column:
                continue
            if column != "*" and column not in allowed_columns.get(table, set()):
                continue
            select_items.append({"table": table, "column": column, "source": "schema_linking"})
    if not select_items:
        select_items = cast(list[dict[str, object]], fallback_plan.get("select", []))

    order_by_items: list[dict[str, object]] = []
    raw_order_by = candidate.get("order_by", [])
    if isinstance(raw_order_by, list):
        for item in raw_order_by:
            if not isinstance(item, dict):
                continue
            table = str(item.get("table") or from_table or "").strip()
            column = str(item.get("column") or "").strip()
            if not table or not column or column not in allowed_columns.get(table, set()):
                continue
            direction = str(item.get("direction") or "ASC").upper()
            order_by_items.append({"table": table, "column": column, "direction": "DESC" if direction == "DESC" else "ASC"})
    if not order_by_items:
        order_by_items = cast(list[dict[str, object]], fallback_plan.get("order_by", []))

    group_by_items: list[dict[str, object]] = []
    raw_group_by = candidate.get("group_by", [])
    if isinstance(raw_group_by, list):
        for item in raw_group_by:
            if isinstance(item, dict):
                table = str(item.get("table") or from_table or "").strip()
                column = str(item.get("column") or "").strip()
                if table and column and column in allowed_columns.get(table, set()):
                    group_by_items.append({"table": table, "column": column})
    params = cast(list[object], fallback_plan.get("params", []))
    where_clauses = cast(list[dict[str, object]], fallback_plan.get("where", []))
    joins = cast(list[dict[str, object]], fallback_plan.get("joins", []))
    distinct = bool(fallback_plan.get("distinct", False) or candidate.get("distinct", False))

    return {
        "select": select_items,
        "from_table": from_table,
        "joins": joins,
        "where": where_clauses,
        "group_by": group_by_items,
        "having": [],
        "order_by": order_by_items,
        "limit": candidate.get("limit") if isinstance(candidate.get("limit"), int) and candidate.get("limit") else fallback_plan.get("limit"),
        "distinct": distinct,
        "params": params,
        "provenance": {
            "select": "schema_linking",
            "from_table": "schema_linking" if from_table else None,
            "joins": "join_path_planning" if joins else None,
            "where": "value_linking" if where_clauses or value_links else None,
            "group_by": "llm_planning" if group_by_items else None,
            "order_by": "llm_planning" if order_by_items else fallback_plan.get("provenance", {}).get("order_by"),
            "limit": "llm_planning" if isinstance(candidate.get("limit"), int) else fallback_plan.get("provenance", {}).get("limit"),
            "distinct": "join_path_planning" if distinct else None,
        },
    }


def _build_query_understanding_prompt(
    question: str,
    fallback: dict[str, Any],
    catalog: SchemaCatalog | None = None,
) -> str:
    parts = [
        "You are a query-understanding planner for an NL2SQL agent.",
        "Return only one JSON object.",
        "Extract user intent without generating SQL.",
        "Keys: intent, target_mentions, condition_mentions, value_mentions, aggregation, group_by, order_by, limit, time_range, requires_join_hint, ambiguities.",
        "condition_mentions must be a list of objects like {\"mention\": \"状态\"}.",
        "order_by items should include table, column, direction when confident.",
    ]

    # 注入当前 schema 的表名和关键业务术语
    if catalog and catalog.tables:
        table_names = [table.name for table in catalog.tables]
        all_business_terms: list[str] = []
        for table in catalog.tables:
            all_business_terms.extend(t for t in table.business_terms if t.strip())
            for column in table.columns:
                all_business_terms.extend(t for t in column.business_terms if t.strip())
        unique_terms = list(dict.fromkeys(all_business_terms))[:20]
        parts.append(f"Available tables: {', '.join(table_names)}")
        if unique_terms:
            parts.append(f"Key business terms: {', '.join(unique_terms)}")

    parts.append(f"Question: {question}")
    parts.append(f"Fallback understanding for reference: {json.dumps(fallback, ensure_ascii=False)}")
    return "\n".join(parts)


def _build_sql_plan_prompt(
    question: str,
    schema_context: list[str],
    query_understanding: dict[str, Any],
    schema_linking: dict[str, Any],
    value_links: list[dict[str, Any]],
    join_path_plan: dict[str, Any],
    fallback_plan: dict[str, Any],
    business_semantic_brief: dict[str, Any] | None = None,
    few_shot_examples: list[dict[str, object]] | None = None,
) -> str:
    semantic_prompt_block = ""
    if business_semantic_brief:
        semantic_prompt_block = cast(str, business_semantic_brief.get("prompt_block", "")).strip()
    few_shot_block = _format_few_shot_examples(few_shot_examples or [])

    parts = [
        "You are a SQL planner for an NL2SQL agent.",
        "Return only one JSON object representing a SQL plan, not SQL text.",
        "Use only tables, columns, joins and values that already appear in the provided context.",
        "Do not invent tables, columns, values, or joins.",
        "Prefer value links for WHERE conditions and join path plan for JOINs.",
        "Use the business semantic brief and examples only to choose intent, metrics, dimensions, and ordering.",
        "Keys: from_table, select, group_by, order_by, limit, distinct.",
        "select items should be objects with table and column.",
        "group_by items should be objects with table and column.",
        "order_by items should be objects with table, column, direction.",
        f"Question: {question}",
        f"Schema context: {json.dumps(schema_context, ensure_ascii=False)}",
        f"Query understanding: {json.dumps(query_understanding, ensure_ascii=False)}",
        f"Schema linking: {json.dumps(schema_linking, ensure_ascii=False)}",
        f"Value links: {json.dumps(value_links, ensure_ascii=False)}",
        f"Join path plan: {json.dumps(join_path_plan, ensure_ascii=False)}",
    ]
    if semantic_prompt_block:
        parts.append(f"Business semantic brief: {semantic_prompt_block}")
    if few_shot_block:
        parts.append(f"Few-shot examples: {few_shot_block}")
    parts.append(f"Deterministic fallback SQL plan: {json.dumps(fallback_plan, ensure_ascii=False)}")
    return "\n".join(parts)


def _build_sql_repair_prompt(
    question: str,
    schema_context: list[str],
    sql_plan: dict[str, Any],
    validation_issues: list[dict[str, Any]],
) -> str:
    return "\n".join([
        "You are a SQL plan repair assistant.",
        "Return only one repaired SQL plan JSON object.",
        "Only repair the provided plan; do not invent tables, columns or joins outside the given schema context.",
        f"Question: {question}",
        f"Schema context: {json.dumps(schema_context, ensure_ascii=False)}",
        f"Current SQL plan: {json.dumps(sql_plan, ensure_ascii=False)}",
        f"Validation issues: {json.dumps(validation_issues, ensure_ascii=False)}",
    ])


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

    aggregation_keywords = [
        # 英文
        "sum", "count", "avg", "max", "min", "total",
        # 中文技术术语
        "总", "统计", "汇总", "平均", "收入", "销售额", "金额",
        # 中文口语化表达
        "哪些卖得好", "卖得好", "最受欢迎", "最热门", "热门", "火爆",
        "销量", "数量最多", "最多", "最少", "总共", "合计",
        "有多少", "多少个", "多少条", "几个", "几条",
        "排行榜", "排行", "排名",
    ]
    if any(keyword in normalized_question for keyword in aggregation_keywords):
        tags.append("aggregation")

    time_range_keywords = [
        # 英文
        "year", "today", "yesterday", "recent", "latest", "newest",
        # 中文技术术语
        "最近", "近 ", "近",
        # 中文口语化时间表达
        "这几天", "这个月", "这周", "今年", "去年", "前天", "昨天", "今天",
        "近期", "刚刚", "刚才", "近日", "日前", "早些时候",
        "天", "周", "月", "年",
    ]
    if any(keyword in normalized_question for keyword in time_range_keywords):
        tags.append("time-range")

    top_n_keywords = [
        # 英文
        "top", "best", "worst",
        # 中文技术术语
        "最高", "最低", "排行", "排名", "前",
        # 中文口语化比较表达
        "最贵", "最便宜", "最划算", "最好", "最差",
        "最受欢迎", "最热门", "最火", "最畅销",
        "好评", "差评", "热门",
    ]
    if any(keyword in normalized_question for keyword in top_n_keywords):
        tags.append("top-n")

    join_keywords = [
        # 英文
        "join",
        # 中文技术术语
        "关联", "同时", "以及", "和", "对应",
        # 中文口语化关联表达
        "属于", "包含", "对应的是", "相关的",
        "一起", "连同", "带上", "附带",
    ]
    if any(keyword in normalized_question for keyword in join_keywords):
        tags.append("join")

    if not tags:
        tags.append("detail")

    return tags


def query_understanding(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    question = state.get("question", "").strip()
    fallback_understanding = _fallback_query_understanding(question, catalog)
    model = llm_service.build_chat_model()
    if model is None:
        return {"query_understanding": fallback_understanding}

    payload = _invoke_model_json(
        model,
        _build_query_understanding_prompt(question, fallback_understanding, catalog),
    )
    if payload is None:
        return {"query_understanding": fallback_understanding}

    return {
        "query_understanding": _normalize_query_understanding_payload(
            question,
            payload,
            fallback_understanding,
        )
    }


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
    catalog: SchemaCatalog | None = None,
) -> str:
    from app.rag.few_shot_manager import FewShotManager

    joined_schema = "\n".join(f"- {item}" for item in schema_context)
    prompt = _load_nl2sql_prompt()
    few_shot_manager = FewShotManager(catalog)
    selected_examples = few_shot_manager.select_examples(question)
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
    query_understanding_result = state.get("query_understanding", {})
    query_schema_plan = (
        await rag_service.build_query_schema_plan(
            question,
            query_understanding=cast(dict[str, object], query_understanding_result),
        )
    ).model_dump()
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


def value_linking(state: AgentState, catalog: SchemaCatalog | None = None) -> AgentState:
    query_understanding_result = state.get("query_understanding", {})
    schema_linking_result = state.get("schema_linking", {})
    value_linking_result = ValueLinker().link(
        cast(dict[str, Any], query_understanding_result),
        cast(dict[str, Any], schema_linking_result),
        catalog,
    )

    return {"value_links": [link.model_dump() for link in value_linking_result.value_links]}


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


def sql_planning(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    from app.rag.few_shot_manager import FewShotManager

    query_understanding = cast(dict[str, Any], state.get("query_understanding", {}))
    schema_linking = cast(dict[str, Any], state.get("schema_linking", {}))
    value_links = cast(list[dict[str, Any]], state.get("value_links", []))
    join_path_plan = cast(dict[str, Any], state.get("join_path_plan", {}))
    business_semantic_brief = cast(dict[str, Any], state.get("business_semantic_brief", {}))
    fallback_plan = SQLPlanner().build(
        query_understanding=query_understanding,
        schema_linking=schema_linking,
        value_links=value_links,
        join_path_plan=join_path_plan,
    ).model_dump()

    model = llm_service.build_chat_model()
    if model is None:
        return {"sql_plan": fallback_plan, "sql_params": cast(list[object], fallback_plan.get("params", []))}

    few_shot_examples = FewShotManager(catalog).select_examples(cast(str, state.get("question", "")))
    payload = _invoke_model_json(
        model,
        _build_sql_plan_prompt(
            state.get("question", ""),
            cast(list[str], state.get("schema_context", [])),
            query_understanding,
            schema_linking,
            value_links,
            join_path_plan,
            fallback_plan,
            business_semantic_brief,
            few_shot_examples,
        ),
    )
    if payload is None:
        return {"sql_plan": fallback_plan, "sql_params": cast(list[object], fallback_plan.get("params", []))}

    sql_plan = _normalize_sql_plan_candidate(
        payload,
        fallback_plan,
        schema_linking,
        value_links,
    )
    return {"sql_plan": sql_plan, "sql_params": cast(list[object], sql_plan.get("params", []))}


def sql_repairing(state: AgentState, llm_service: LLMService) -> AgentState:
    retry_count = state.get("retry_count", 0) + 1
    repair_attempts = state.get("repair_attempts", 0) + 1
    validation_issues = cast(list[dict[str, Any]], state.get("validation_issues", []))
    current_plan = cast(dict[str, Any], state.get("sql_plan", {}))
    repair_result = SQLRepairer().repair(current_plan, validation_issues)
    debug_trace = dict(state.get("debug_trace", {}))

    if not repair_result.repaired and repair_result.fatal and validation_issues:
        model = llm_service.build_chat_model()
        if model is not None:
            payload = _invoke_model_json(
                model,
                _build_sql_repair_prompt(
                    state.get("question", ""),
                    cast(list[str], state.get("schema_context", [])),
                    current_plan,
                    validation_issues,
                ),
            )
            if payload is not None:
                repaired_plan = _normalize_sql_plan_candidate(
                    payload,
                    current_plan,
                    cast(dict[str, Any], state.get("schema_linking", {})),
                    cast(list[dict[str, Any]], state.get("value_links", [])),
                )
                debug_trace["last_repair"] = {
                    "attempt": repair_attempts,
                    "repaired": True,
                    "fatal": False,
                    "summary": "已通过 LLM 重写 SQL Plan。",
                    "mode": "llm",
                    "validation_errors": state.get("validation_errors", []),
                    "validation_issues": validation_issues,
                }
                return {
                    "sql_plan": repaired_plan,
                    "sql_params": cast(list[object], repaired_plan.get("params", [])),
                    "retry_count": retry_count,
                    "repair_attempts": repair_attempts,
                    "debug_trace": debug_trace,
                    "validation_errors": [],
                    "validation_issues": [],
                }

    debug_trace["last_repair"] = {
        "attempt": repair_attempts,
        "repaired": repair_result.repaired,
        "fatal": repair_result.fatal,
        "summary": repair_result.summary,
        "mode": "deterministic",
        "validation_errors": state.get("validation_errors", []),
        "validation_issues": validation_issues,
    }

    if repair_result.fatal:
        return {
            "retry_count": retry_count,
            "repair_attempts": repair_attempts,
            "debug_trace": debug_trace,
            "status": "error",
            "validation_errors": [],
            "explanation": repair_result.summary,
            "execution_summary": repair_result.summary,
        }

    return {
        "sql_plan": repair_result.sql_plan,
        "sql_params": cast(list[object], repair_result.sql_plan.get("params", [])),
        "retry_count": retry_count,
        "repair_attempts": repair_attempts,
        "debug_trace": debug_trace,
        "validation_errors": [],
        "validation_issues": [],
    }


def generate_sql(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    sql_plan = cast(dict[str, Any], state.get("sql_plan", {}))
    generated_sql = SQLGenerator().generate(sql_plan)
    if generated_sql is not None:
        return {
            "sql": generated_sql.sql,
            "sql_params": generated_sql.params,
            "status": "ready",
            "used_fallback": False,
            "explanation": "已根据结构化 SQL Plan 生成参数化 SQL，接下来会进入只读安全校验。",
        }

    question = state.get("question", "")
    return {
        "sql": build_fallback_sql(question, catalog),
        "sql_params": [],
        "status": "mock",
        "used_fallback": True,
        "explanation": "结构化 SQL Plan 无法渲染为可执行 SQL，系统已回退到稳定的示例 SQL。",
    }


def validate_sql(state: AgentState, validator: SQLValidator) -> AgentState:
    # 只允许只读 SQL，校验失败则标记重试计数，由 Graph 条件路由决定是否重新生成。
    sql = state.get("sql", "")
    retry_count = state.get("retry_count", 0)
    sql_plan = cast(dict[str, object], state.get("sql_plan", {}))
    sql_params = cast(list[object], state.get("sql_params", []))

    validation_issues: list[dict[str, Any]] = []
    try:
        validator.validate_read_only(sql)
        validation_issues.extend(
            validator.validate_plan_provenance(sql_plan=sql_plan, params=sql_params)
        )
        validation_issues.extend(
            validator.validate_sql_matches_plan(sql=sql, sql_plan=sql_plan, params=sql_params)
        )
    except DangerousSQLError as error:
        validation_issues.append(
            {
                "level": "error",
                "code": "READ_ONLY_VALIDATION_FAILED",
                "message": str(error),
                "repairable": False,
            }
        )

    if not validation_issues:
        return {"validation_errors": [], "validation_issues": []}

    validation_errors = [str(issue.get("message", "SQL validation failed.")) for issue in validation_issues]
    return {
        "validation_errors": validation_errors,
        "validation_issues": validation_issues,
        "retry_count": retry_count,
        "explanation": f"SQL 校验未通过（{validation_errors[0]}），将根据错误类型决定是否修复。",
    }


async def execute_sql(state: AgentState, executor: SQLExecutor) -> AgentState:
    # 在 Graph 内执行 SQL，填充结果字段。
    sql = state.get("sql", "")
    sql_params = cast(list[object], state.get("sql_params", []))
    started_at = time.monotonic()

    try:
        result = await executor.execute(sql, params=sql_params)
        elapsed_ms = (time.monotonic() - started_at) * 1000
        execution_summary = result.execution_summary or ""
        is_error = execution_summary.startswith("查询执行失败") or execution_summary.startswith("查询执行超时")
        return {
            "status": "error" if is_error else state.get("status", "ready"),
            "rows": [] if is_error else result.rows,
            "columns": [] if is_error else result.columns,
            "row_count": 0 if is_error else result.row_count,
            "truncated": False if is_error else result.truncated,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_summary": execution_summary,
            "explanation": f"SQL 执行出错：{execution_summary}" if is_error else state.get("explanation", ""),
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
    execution_summary = cast(str, state.get("execution_summary", ""))
    status = cast(str, state.get("status", "ready"))

    if validation_errors:
        status = "error"
        explanation = f"SQL 校验未通过：{validation_errors[0]}。"
    elif execution_summary.startswith("查询执行失败") or execution_summary.startswith("查询执行超时"):
        status = "error"
        explanation = execution_summary

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
    last_repair = debug_trace.get("last_repair")
    repair_attempt_trace = [last_repair] if isinstance(last_repair, dict) else []
    schema_linking_debug = state.get("schema_linking", {})
    join_path_debug = state.get("join_path_plan", {})
    debug_trace.update(
        {
            "query_understanding": state.get("query_understanding", {}),
            "schema_linking": schema_linking_debug,
            "schema_links": schema_linking_debug,
            "value_links": state.get("value_links", []),
            "join_path_plan": join_path_debug,
            "join_paths": join_path_debug,
            "semantic_brief": state.get("business_semantic_brief", {}),
            "sql_plan": state.get("sql_plan", {}),
            "validation": {"errors": validation_errors, "issues": validation_issues},
            "validation_errors": validation_errors,
            "validation_issues": validation_issues,
            "repair_attempts": repair_attempt_trace,
            "fallback": {"used": state.get("used_fallback", False)},
            "execution": {
                "row_count": state.get("row_count", 0),
                "truncated": state.get("truncated", False),
                "execution_time_ms": execution_time_ms,
                "summary": execution_summary,
            },
            "schema_context_count": len(schema_context),
            "confidence": join_path_debug.get("confidence", 0.0),
        }
    )

    result: AgentState = {"explanation": explanation, "debug_trace": debug_trace, "status": status}
    if status == "error":
        result["rows"] = []
        result["columns"] = []
        result["row_count"] = 0
        result["truncated"] = False
    return result
