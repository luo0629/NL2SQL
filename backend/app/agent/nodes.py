from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, cast

from app.agent.state import AgentState
from app.config import get_settings
from app.database.executor import SQLExecutor
from app.rag.schema_models import SchemaCatalog, SchemaTable
from app.services.llm_service import LLMService
from app.utils.exceptions import DangerousSQLError
from app.validator.sql_validator import SQLValidator


logger = logging.getLogger(__name__)


def _extract_text(content: str | list[str | dict[str, str]]) -> str:
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
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


def _invoke_model_text(model: Any, prompt: str) -> str | None:
    try:
        response = model.invoke(prompt)
        return _extract_text(cast(str | list[str | dict[str, str]], response.content)).strip()
    except Exception:
        return None


def _invoke_model_json(model: Any, prompt: str) -> dict[str, Any] | None:
    content = _invoke_model_text(model, prompt)
    if content is None:
        return None
    return _extract_json_object(content)


async def _ainvoke_model_text(
    model: Any,
    prompt: str,
    *,
    timeout_seconds: float | None = None,
    stage: str,
) -> tuple[str | None, str | None]:
    started_at = time.monotonic()
    try:
        if hasattr(model, "ainvoke"):
            invocation = model.ainvoke(prompt)
        else:
            invocation = asyncio.to_thread(model.invoke, prompt)

        if timeout_seconds is not None and timeout_seconds > 0:
            response = await asyncio.wait_for(invocation, timeout=timeout_seconds)
        else:
            response = await invocation
        elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.info("llm.%s.end duration_ms=%.2f", stage, elapsed_ms)
        return _extract_text(cast(str | list[str | dict[str, str]], response.content)).strip(), None
    except TimeoutError:
        logger.warning("llm.%s.timeout timeout_seconds=%.2f", stage, timeout_seconds or 0)
        return None, "timeout"
    except Exception as error:
        logger.warning("llm.%s.error error_class=%s", stage, error.__class__.__name__)
        return None, error.__class__.__name__


async def _ainvoke_model_json(
    model: Any,
    prompt: str,
    *,
    timeout_seconds: float | None = None,
    stage: str,
) -> tuple[dict[str, Any] | None, str | None]:
    content, error = await _ainvoke_model_text(
        model,
        prompt,
        timeout_seconds=timeout_seconds,
        stage=stage,
    )
    if content is None:
        return None, error
    payload = _extract_json_object(content)
    if payload is None:
        return None, "invalid_json"
    return payload, None


def _quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _normalize_sql(candidate: str) -> str:
    cleaned = candidate.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].strip()
    if not cleaned.endswith(";"):
        cleaned = f"{cleaned};"
    return cleaned


def _catalog_tables(catalog: SchemaCatalog | None) -> list[SchemaTable]:
    return catalog.tables if catalog and catalog.tables else []


def _table_score(question: str, table: SchemaTable) -> int:
    normalized = question.lower()
    score = 0
    candidates = [table.name, table.description or "", *table.aliases, *table.business_terms]
    for term in candidates:
        term = term.strip()
        if term and term.lower() in normalized:
            score += 6 if term == table.name else 4
    for column in table.columns:
        column_terms = [column.name, column.description or "", *(column.business_terms or [])]
        for term in column_terms:
            term = term.strip()
            if term and term.lower() in normalized:
                score += 2
    return score


def _fallback_relevant_tables(question: str, catalog: SchemaCatalog | None, limit: int = 4) -> list[str]:
    tables = _catalog_tables(catalog)
    if not tables:
        return []
    scored = [(table.name, _table_score(question, table), index) for index, table in enumerate(tables)]
    selected = [name for name, score, _index in sorted(scored, key=lambda item: (-item[1], item[2])) if score > 0]
    if not selected:
        selected = [table.name for table in tables]
    return selected[:limit]


def _build_intent_prompt(question: str, table_names: list[str], catalog: SchemaCatalog | None) -> str:
    table_summaries: list[str] = []
    for table in _catalog_tables(catalog):
        terms = [table.description or "", *table.aliases, *table.business_terms]
        summary = "、".join(term for term in terms if term) or "无补充说明"
        table_summaries.append(f"- {table.name}: {summary}")
    return "\n".join(
        [
            "你是 NL2SQL 的 intent_parser。只返回一个 JSON 对象，不要生成 SQL。",
            "任务：用中文概括用户查询意图，并从真实表名列表中选择 1 到 4 张最相关表。",
            "JSON 格式：{\"intent\": \"...\", \"relevant_tables\": [\"table_a\"]}",
            "relevant_tables 只能使用给定表名，不能编造表。",
            f"真实表名列表：{', '.join(table_names) if table_names else '(空)'}",
            "表说明：",
            "\n".join(table_summaries[:80]),
            f"用户问题：{question}",
        ]
    )


def _build_intent_result(
    state: AgentState,
    payload: dict[str, Any] | None,
    catalog: SchemaCatalog | None,
    *,
    source: str,
    llm_error: str | None = None,
) -> AgentState:
    question = (state.get("user_input") or state.get("question") or "").strip()
    table_names = [table.name for table in _catalog_tables(catalog)]
    allowed = set(table_names)
    fallback_tables = _fallback_relevant_tables(question, catalog)
    intent = f"查询需求：{question}" if question else "查询数据库信息"
    relevant_tables = fallback_tables

    if payload is not None:
        candidate_intent = payload.get("intent")
        if isinstance(candidate_intent, str) and candidate_intent.strip():
            intent = candidate_intent.strip()
        raw_tables = payload.get("relevant_tables", [])
        if isinstance(raw_tables, list):
            filtered = [str(item).strip() for item in raw_tables if str(item).strip() in allowed]
            if filtered:
                relevant_tables = list(dict.fromkeys(filtered))[:4]

    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["intent_parser"] = {
        "source": source,
        "available_table_count": len(table_names),
        "relevant_tables": relevant_tables,
        "llm_error": llm_error,
    }
    # 兼容旧 debug contract：query_understanding 现在指向简化意图结果。
    debug_trace["query_understanding"] = {
        "intent": intent,
        "relevant_tables": relevant_tables,
        "source": source,
    }
    query_understanding_payload = {
        "intent": intent,
        "relevant_tables": relevant_tables,
        "source": source,
    }
    return {
        "user_input": question,
        "question": question,
        "intent": intent,
        "relevant_tables": relevant_tables,
        "available_tables": table_names,
        "query_understanding": query_understanding_payload,
        "debug_trace": debug_trace,
    }


def intent_parser(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    question = (state.get("user_input") or state.get("question") or "").strip()
    table_names = [table.name for table in _catalog_tables(catalog)]
    model = llm_service.build_chat_model()
    payload: dict[str, Any] | None = None
    source = "deterministic"
    if model is not None:
        payload = _invoke_model_json(model, _build_intent_prompt(question, table_names, catalog))
        source = "llm" if payload is not None else "deterministic"
    return _build_intent_result(state, payload, catalog, source=source)


async def async_intent_parser(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    question = (state.get("user_input") or state.get("question") or "").strip()
    table_names = [table.name for table in _catalog_tables(catalog)]
    model = llm_service.build_chat_model()
    payload: dict[str, Any] | None = None
    llm_error: str | None = None
    source = "deterministic"
    if model is not None:
        settings = get_settings()
        payload, llm_error = await _ainvoke_model_json(
            model,
            _build_intent_prompt(question, table_names, catalog),
            timeout_seconds=settings.agent_llm_node_timeout_seconds,
            stage="intent_parser",
        )
        source = "llm" if payload is not None else "deterministic"
    return _build_intent_result(state, payload, catalog, source=source, llm_error=llm_error)


def _format_table_schema(
    table: SchemaTable,
    catalog: SchemaCatalog | None,
    selected_table_names: set[str] | None = None,
) -> str:
    lines = [f"Table {_quote_identifier(table.name)}"]
    if table.description:
        lines.append(f"Comment: {table.description}")
    if table.primary_keys:
        lines.append(f"Primary keys: {', '.join(_quote_identifier(key) for key in table.primary_keys)}")
    lines.append("Columns:")
    for column in table.columns:
        attrs = [column.data_type]
        attrs.append("NULL" if column.nullable else "NOT NULL")
        if column.is_primary_key:
            attrs.append("PRIMARY KEY")
        if column.default is not None:
            attrs.append(f"default={column.default}")
        if column.semantic_role:
            attrs.append(f"role={column.semantic_role}")
        if column.description:
            attrs.append(f"comment={column.description}")
        if column.business_terms:
            attrs.append(f"terms={', '.join(column.business_terms)}")
        lines.append(f"- {_quote_identifier(column.name)} ({'; '.join(attrs)})")

    selected_table_names = selected_table_names or {table.name}
    relations = [] if catalog is None else [
        relation for relation in catalog.relations
        if (relation.from_table == table.name or relation.to_table == table.name)
        and relation.from_table in selected_table_names
        and relation.to_table in selected_table_names
    ]
    if relations:
        lines.append("Relations:")
        for relation in relations:
            hint = f"; hint={relation.join_hint}" if relation.join_hint else ""
            lines.append(
                f"- {_quote_identifier(relation.from_table)}.{_quote_identifier(relation.from_column)} -> "
                f"{_quote_identifier(relation.to_table)}.{_quote_identifier(relation.to_column)}"
                f" ({relation.relation_type or 'relation'}{hint})"
            )
    return "\n".join(lines)


def schema_retriever(state: AgentState, catalog: SchemaCatalog | None = None) -> AgentState:
    relevant = state.get("relevant_tables", [])
    relevant_set = set(relevant)
    selected_tables = [table for table in _catalog_tables(catalog) if table.name in relevant_set]
    if not selected_tables:
        selected_tables = _catalog_tables(catalog)[:4]
    selected_table_names = {table.name for table in selected_tables}
    schema_context = "\n\n".join(
        _format_table_schema(table, catalog, selected_table_names)
        for table in selected_tables
    )
    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["schema_retriever"] = {
        "tables": [table.name for table in selected_tables],
        "schema_context_chars": len(schema_context),
    }
    # 兼容旧 debug contract。
    debug_trace["schema_links"] = {"matched_tables": [table.name for table in selected_tables]}
    debug_trace["value_links"] = []
    debug_trace["join_paths"] = {"relations": len(catalog.relations) if catalog else 0}
    matched_table_names = [table.name for table in selected_tables]
    return {
        "schema_context": schema_context,
        "relevant_tables": matched_table_names,
        "query_schema_plan": {"schema_context": schema_context, "matched_tables": matched_table_names},
        "schema_linking": {"matched_tables": matched_table_names, "linking_summary": f"命中表: {', '.join(matched_table_names)}。"},
        "join_path_plan": {"relations": len(catalog.relations) if catalog else 0},
        "business_semantic_brief": {"intent": state.get("intent", "")},
        "linking_summary": f"命中表: {', '.join(matched_table_names)}。",
        "join_planning_summary": "已按相关表保留真实 schema 关系信息。",
        "value_links": [],
        "debug_trace": debug_trace,
    }


def _build_sql_generation_prompt(state: AgentState) -> str:
    retry_count = state.get("retry_count", 0)
    previous_sql = state.get("generated_sql") or state.get("sql") or ""
    validation_error = state.get("validation_error", "")
    parts = [
        "你是 MySQL NL2SQL 生成器。只输出一条 SQL，不要解释，不要 Markdown。",
        "硬性规则：只能生成只读 SELECT/WITH 查询；禁止 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/EXEC/SLEEP/BENCHMARK。",
        "所有表名和字段名必须用反引号包裹。",
        "只能使用 schema_context 中出现的表和字段；不确定时选择最小可执行查询。",
        "如需要 LIMIT，必须同时给出稳定 ORDER BY。",
        "默认添加合理 LIMIT 200，除非用户明确要求更小。",
        f"用户问题：{state.get('user_input') or state.get('question') or ''}",
        f"意图：{state.get('intent', '')}",
        f"相关表：{', '.join(state.get('relevant_tables', []))}",
        "schema_context:",
        state.get("schema_context", ""),
    ]
    if validation_error:
        parts.extend(
            [
                f"这是第 {retry_count + 1} 次生成。上一轮 SQL 校验失败，请针对性修复。",
                f"上一轮 SQL：{previous_sql}",
                f"校验错误：{validation_error}",
            ]
        )
    return "\n".join(parts)


def build_fallback_sql(question: str, catalog: SchemaCatalog | None = None, relevant_tables: list[str] | None = None) -> str:
    tables = _catalog_tables(catalog)
    if not tables:
        return "SELECT 1 AS result;"
    table_by_name = {table.name: table for table in tables}
    selected_name = None
    for name in relevant_tables or []:
        if name in table_by_name:
            selected_name = name
            break
    if selected_name is None:
        selected = _fallback_relevant_tables(question, catalog, limit=1)
        selected_name = selected[0] if selected else tables[0].name
    table = table_by_name[selected_name]
    columns = [column.name for column in table.columns[:5]] or ["*"]
    select_expr = ", ".join("*" if column == "*" else _quote_identifier(column) for column in columns)
    order_column = next((column.name for column in table.columns if column.is_primary_key), None) or (table.columns[0].name if table.columns else None)
    sql = f"SELECT {select_expr} FROM {_quote_identifier(table.name)}"
    if order_column:
        sql += f" ORDER BY {_quote_identifier(order_column)} DESC"
    sql += " LIMIT 20;"
    return sql


def _build_sql_generator_result(
    state: AgentState,
    catalog: SchemaCatalog | None,
    generated_sql: str | None,
    *,
    llm_error: str | None = None,
) -> AgentState:
    used_fallback = False
    status = "ready"
    if not generated_sql:
        generated_sql = build_fallback_sql(
            state.get("user_input") or state.get("question") or "",
            catalog,
            state.get("relevant_tables", []),
        )
        used_fallback = True
        status = "mock"

    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["sql_generator"] = {
        "retry_count": state.get("retry_count", 0),
        "used_fallback": used_fallback,
        "had_validation_error": bool(state.get("validation_error")),
        "llm_error": llm_error,
    }
    debug_trace["sql_plan"] = {
        "mode": "direct_sql_generation",
        "removed": "SemanticQuery/sql_plan 主路径已旁路",
    }
    return {
        "generated_sql": generated_sql,
        "sql": generated_sql,
        "sql_params": [],
        "status": cast(Any, status),
        "used_fallback": used_fallback,
        "validation_error": "",
        "validation_errors": [],
        "validation_issues": [],
        "debug_trace": debug_trace,
        "explanation": "已基于真实 schema context 直接生成 MySQL 只读 SQL。",
    }


def sql_generator(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    model = llm_service.build_chat_model()
    generated_sql: str | None = None
    if model is not None:
        content = _invoke_model_text(model, _build_sql_generation_prompt(state))
        if content:
            generated_sql = _normalize_sql(content)
    return _build_sql_generator_result(state, catalog, generated_sql)


async def async_sql_generator(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    model = llm_service.build_chat_model()
    generated_sql: str | None = None
    llm_error: str | None = None
    if model is not None:
        settings = get_settings()
        content, llm_error = await _ainvoke_model_text(
            model,
            _build_sql_generation_prompt(state),
            timeout_seconds=settings.agent_llm_node_timeout_seconds,
            stage="sql_generator",
        )
        if content:
            generated_sql = _normalize_sql(content)
    return _build_sql_generator_result(state, catalog, generated_sql, llm_error=llm_error)


def _should_run_mysql_explain() -> bool:
    database_url = (get_settings().database_url or "").lower()
    return "mysql" in database_url or "asyncmy" in database_url or "pymysql" in database_url


async def sql_validator(state: AgentState, validator: SQLValidator, executor: SQLExecutor) -> AgentState:
    sql = state.get("sql") or state.get("generated_sql") or ""
    retry_count = state.get("retry_count", 0)
    debug_trace = dict(state.get("debug_trace", {}))
    settings = get_settings()
    should_explain = _should_run_mysql_explain()
    started_at = time.monotonic()
    try:
        validator.validate_read_only(sql)
        if should_explain:
            await executor.explain(
                sql,
                params=state.get("sql_params", []),
                timeout_seconds=settings.sql_explain_timeout_seconds,
            )
    except DangerousSQLError as error:
        message = str(error)
    except TimeoutError:
        message = "EXPLAIN 预检超时"
    except Exception as error:
        message = f"EXPLAIN 预检失败：{error.__class__.__name__}"
    else:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        debug_trace["sql_validator"] = {
            "passed": True,
            "retry_count": retry_count,
            "explain": "mysql" if should_explain else "skipped_non_mysql",
            "duration_ms": round(elapsed_ms, 2),
            "timeout_seconds": settings.sql_explain_timeout_seconds if should_explain else None,
        }
        logger.info("agent.sql_validator.end passed=true duration_ms=%.2f", elapsed_ms)
        return {"validation_error": "", "validation_errors": [], "validation_issues": [], "debug_trace": debug_trace}

    next_retry_count = retry_count + 1
    elapsed_ms = (time.monotonic() - started_at) * 1000
    debug_trace["sql_validator"] = {
        "passed": False,
        "retry_count": retry_count,
        "next_retry_count": next_retry_count,
        "error": message,
        "duration_ms": round(elapsed_ms, 2),
        "timeout_seconds": settings.sql_explain_timeout_seconds if should_explain else None,
    }
    logger.info("agent.sql_validator.end passed=false duration_ms=%.2f error=%s", elapsed_ms, message)
    return {
        "validation_error": message,
        "validation_errors": [message],
        "validation_issues": [
            {
                "level": "error",
                "code": "SQL_VALIDATION_OR_EXPLAIN_FAILED",
                "message": message,
                "repairable": next_retry_count < state.get("max_retries", 3),
            }
        ],
        "retry_count": next_retry_count,
        "debug_trace": debug_trace,
        "explanation": f"SQL 验证未通过：{message}",
    }


async def sql_executor(state: AgentState, executor: SQLExecutor) -> AgentState:
    sql = state.get("sql") or state.get("generated_sql") or ""
    started_at = time.monotonic()
    try:
        settings = get_settings()
        result = await executor.execute(
            sql,
            params=state.get("sql_params", []),
            max_rows=settings.query_result_limit,
            timeout_seconds=settings.query_execution_timeout_seconds,
        )
        elapsed_ms = (time.monotonic() - started_at) * 1000
        summary = result.execution_summary or ""
        is_error = summary.startswith("查询执行失败") or summary.startswith("查询执行超时")
        debug_trace = dict(state.get("debug_trace", {}))
        debug_trace["sql_executor"] = {
            "row_count": 0 if is_error else result.row_count,
            "columns": [] if is_error else result.columns,
            "execution_time_ms": round(elapsed_ms, 2),
            "status": "error" if is_error else "ready",
            "timeout_seconds": settings.query_execution_timeout_seconds,
        }
        logger.info(
            "agent.sql_executor.end status=%s row_count=%s duration_ms=%.2f",
            "error" if is_error else "ready",
            0 if is_error else result.row_count,
            elapsed_ms,
        )
        return {
            "status": "error" if is_error else "ready",
            "query_result": [] if is_error else result.rows,
            "rows": [] if is_error else result.rows,
            "columns": [] if is_error else result.columns,
            "row_count": 0 if is_error else result.row_count,
            "truncated": False if is_error else result.truncated,
            "execution_summary": summary,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_error": {"summary": summary} if is_error else {},
            "debug_trace": debug_trace,
        }
    except Exception as error:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        summary = f"查询执行失败：{error.__class__.__name__}"
        debug_trace = dict(state.get("debug_trace", {}))
        debug_trace["sql_executor"] = {
            "row_count": 0,
            "columns": [],
            "execution_time_ms": round(elapsed_ms, 2),
            "status": "error",
        }
        return {
            "status": "error",
            "query_result": [],
            "rows": [],
            "columns": [],
            "row_count": 0,
            "truncated": False,
            "execution_summary": summary,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_error": {"summary": summary},
            "debug_trace": debug_trace,
        }


def _build_formatter_prompt(state: AgentState) -> str:
    preview_rows = state.get("rows", [])[:20]
    return "\n".join(
        [
            "你是 SQL 查询结果解读助手。用中文自然语言直接回答用户问题，不要泄露内部错误细节。",
            "可以简要提到结果行数；不要隐藏 SQL 是否生成，SQL 会由系统单独展示。",
            f"用户问题：{state.get('user_input') or state.get('question') or ''}",
            f"意图：{state.get('intent', '')}",
            f"SQL：{state.get('sql', '')}",
            f"执行摘要：{state.get('execution_summary', '')}",
            f"行数：{state.get('row_count', 0)}",
            f"结果预览：{json.dumps(preview_rows, ensure_ascii=False, default=str)}",
        ]
    )


def _default_final_answer(state: AgentState) -> tuple[str, str, str]:
    status = state.get("status", "ready")
    validation_error = state.get("validation_error", "")
    execution_summary = state.get("execution_summary", "")
    row_count = state.get("row_count", 0)

    if validation_error:
        status = "error"
        execution_summary = execution_summary or "SQL 验证失败，已停止执行。"
        final_answer = "抱歉，生成的 SQL 未通过安全或语法预检，已停止执行。请换一种更明确的问法后重试。"
    elif status == "error":
        final_answer = "抱歉，查询执行失败。系统已返回脱敏后的错误摘要，请稍后重试或调整问题。"
    elif row_count == 0:
        final_answer = "查询执行成功，但没有找到符合条件的数据。可以尝试放宽筛选条件或调整时间范围。"
        execution_summary = execution_summary or "查询执行成功，但没有返回记录。"
    else:
        final_answer = f"查询执行成功，共返回 {row_count} 行结果。"
    return cast(str, status), execution_summary, final_answer


def _build_formatter_result(
    state: AgentState,
    *,
    status: str,
    execution_summary: str,
    final_answer: str,
    llm_error: str | None = None,
) -> AgentState:
    row_count = state.get("row_count", 0)
    explanation = final_answer
    if state.get("execution_time_ms") is not None:
        explanation = f"{explanation} 执行耗时：{state.get('execution_time_ms'):.0f}ms。"

    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["validation_errors"] = state.get("validation_errors", [])
    debug_trace["validation_issues"] = state.get("validation_issues", [])
    debug_trace["result_formatter"] = {
        "status": status,
        "row_count": row_count,
        "has_validation_error": bool(state.get("validation_error", "")),
        "llm_error": llm_error,
    }
    debug_trace["execution"] = {
        "row_count": row_count,
        "truncated": state.get("truncated", False),
        "execution_time_ms": state.get("execution_time_ms"),
        "summary": execution_summary,
    }
    debug_trace["fallback"] = {"used": False}
    debug_trace["direct_sql_fallback"] = {"used": state.get("used_fallback", False)}

    result: AgentState = {
        "status": cast(Any, status),
        "final_answer": final_answer,
        "explanation": explanation,
        "execution_summary": execution_summary,
        "debug_trace": debug_trace,
    }
    if status == "error":
        result.update({"rows": [], "columns": [], "row_count": 0, "query_result": []})
    return result


def result_formatter(state: AgentState, llm_service: LLMService) -> AgentState:
    status, execution_summary, final_answer = _default_final_answer(state)
    model = llm_service.build_chat_model()
    if model is not None and not state.get("validation_error", ""):
        formatted = _invoke_model_text(model, _build_formatter_prompt(state))
        if formatted:
            final_answer = formatted.strip()
    return _build_formatter_result(
        state,
        status=status,
        execution_summary=execution_summary,
        final_answer=final_answer,
    )


async def async_result_formatter(state: AgentState, llm_service: LLMService) -> AgentState:
    status, execution_summary, final_answer = _default_final_answer(state)
    llm_error: str | None = None
    model = llm_service.build_chat_model()
    if model is not None and not state.get("validation_error", ""):
        settings = get_settings()
        formatted, llm_error = await _ainvoke_model_text(
            model,
            _build_formatter_prompt(state),
            timeout_seconds=settings.result_formatter_llm_timeout_seconds,
            stage="result_formatter",
        )
        if formatted:
            final_answer = formatted.strip()
    return _build_formatter_result(
        state,
        status=status,
        execution_summary=execution_summary,
        final_answer=final_answer,
        llm_error=llm_error,
    )


def _detect_question_tags(question: str) -> list[str]:
    normalized = question.strip().lower()
    tags: list[str] = []
    if any(keyword in normalized for keyword in ["sum", "count", "avg", "total", "总", "统计", "汇总", "平均", "收入", "销售额", "金额", "卖得好", "最受欢迎", "最热门", "销量", "最多", "最少", "有多少", "多少个", "排行", "排名"]):
        tags.append("aggregation")
    if any(keyword in normalized for keyword in ["year", "today", "yesterday", "recent", "latest", "最近", "近期", "这几天", "这个月", "这周", "今年", "去年", "昨天", "今天", "30天", "天", "周", "月", "年"]):
        tags.append("time-range")
    if any(keyword in normalized for keyword in ["top", "best", "worst", "最高", "最低", "排行", "排名", "前", "最贵", "最便宜", "最好", "最差", "最受欢迎", "最热门", "最火"]):
        tags.append("top-n")
    if any(keyword in normalized for keyword in ["join", "关联", "同时", "以及", "和", "对应", "属于", "包含", "哪些口味", "连同"]):
        tags.append("join")
    return tags or ["detail"]


def _infer_primary_table(question: str, catalog: SchemaCatalog | None = None) -> str | None:
    tables = _catalog_tables(catalog)
    if not tables:
        return None
    if not question.strip():
        return tables[0].name
    scored = [(table.name, _table_score(question, table), index) for index, table in enumerate(tables)]
    name, score, _index = sorted(scored, key=lambda item: (-item[1], item[2]))[0]
    return name if score > 0 else None


def _extract_catalog_business_terms(catalog: SchemaCatalog | None) -> tuple[list[str], list[str]]:
    if not catalog or not catalog.tables:
        return [], []
    terms: list[str] = []
    markers: list[str] = []
    for table in catalog.tables:
        terms.extend(item for item in [table.description, *table.aliases, *table.business_terms] if item)
        for column in table.columns:
            terms.extend(item for item in [column.description, *column.business_terms] if item)
            if column.semantic_role in {"dimension", "foreign_key", "timestamp"}:
                markers.extend(item for item in [column.description, *column.business_terms] if item)
    return list(dict.fromkeys(terms)), list(dict.fromkeys(markers))


def _fallback_query_understanding(question: str, catalog: SchemaCatalog | None = None) -> dict[str, Any]:
    tags = _detect_question_tags(question)
    terms, markers = _extract_catalog_business_terms(catalog)
    target_mentions = [term for term in (terms or ["客户", "用户", "订单", "菜品", "金额"]) if term and term in question]
    condition_mentions = [{"mention": marker} for marker in (markers or ["状态", "分类", "价格", "金额", "时间"]) if marker and marker in question]
    limit_match = re.search(r"(?:top\s*|前\s*)(\d+)", question.lower())
    order_by: list[dict[str, object]] = []
    if any(keyword in question.lower() for keyword in ["最高", "最多", "top", "desc", "最贵", "最热门"]):
        order_by.append({"direction": "DESC"})
    elif any(keyword in question.lower() for keyword in ["最低", "最少", "asc", "最便宜"]):
        order_by.append({"direction": "ASC"})
    return {
        "intent": "aggregate" if "aggregation" in tags else "select",
        "target_mentions": target_mentions,
        "condition_mentions": condition_mentions,
        "value_mentions": re.findall(r"[“”‘’\"']([^“”‘’\"']+)[“”‘’\"']", question),
        "aggregation": {"type": "auto"} if "aggregation" in tags else None,
        "group_by": [],
        "order_by": order_by,
        "limit": int(limit_match.group(1)) if limit_match else None,
        "time_range": {"type": "relative"} if "time-range" in tags else None,
        "requires_join_hint": "join" in tags,
        "tags": tags,
        "source": "deterministic",
    }


def _load_nl2sql_prompt() -> str:
    from functools import lru_cache
    from pathlib import Path

    @lru_cache(maxsize=1)
    def _load() -> str:
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "nl2sql_prompt.txt"
        return prompt_path.read_text(encoding="utf-8").strip()

    return _load()


def _load_few_shot_examples() -> list[dict[str, object]]:
    from functools import lru_cache
    from pathlib import Path

    @lru_cache(maxsize=1)
    def _load() -> list[dict[str, object]]:
        examples_path = Path(__file__).resolve().parents[1] / "prompts" / "few_shot_examples.json"
        if not examples_path.exists():
            return []
        raw_examples = json.loads(examples_path.read_text(encoding="utf-8"))
        if not isinstance(raw_examples, list):
            return []
        return [example for example in raw_examples if isinstance(example, dict) and isinstance(example.get("question"), str) and isinstance(example.get("sql"), str)]

    return _load()


def _select_few_shot_examples(question: str, limit: int = 3) -> list[dict[str, object]]:
    question_tags = set(_detect_question_tags(question))
    examples = _load_few_shot_examples()
    scored = []
    for index, example in enumerate(examples):
        example_tags = set(cast(list[str], example.get("tags", [])))
        overlap = len(question_tags & example_tags)
        if overlap:
            scored.append((overlap, index, example))
    if scored:
        return [example for _score, _index, example in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]
    return examples[:limit]


def _build_prompt(
    question: str,
    schema_context: list[str],
    business_semantic_brief: dict[str, Any] | None = None,
    join_path_plan: dict[str, Any] | None = None,
    schema_linking: dict[str, Any] | None = None,
    catalog: SchemaCatalog | None = None,
) -> str:
    joined_schema = "\n".join(f"- {item}" for item in schema_context)
    few_shot = _select_few_shot_examples(question)
    formatted_examples = "\n\n".join(f"Example {index}:\nQuestion: {example['question']}\nSQL:\n{example['sql']}" for index, example in enumerate(few_shot, 1))
    parts = [_load_nl2sql_prompt()]
    if formatted_examples:
        parts.append(f"## 6. Reference examples\nUse the following examples only as style and structure references.\n\n{formatted_examples}")
    if business_semantic_brief and business_semantic_brief.get("prompt_block"):
        parts.append(str(business_semantic_brief["prompt_block"]))
    if schema_linking:
        lines = ["## Schema linking plan"]
        if schema_linking.get("linking_summary"):
            lines.append(f"Summary: {schema_linking['linking_summary']}")
        parts.append("\n".join(lines))
    if join_path_plan:
        lines = ["## Join path plan"]
        if join_path_plan.get("plan_confidence"):
            lines.append(f"Confidence: {join_path_plan['plan_confidence']}")
        if join_path_plan.get("planning_summary"):
            lines.append(f"Summary: {join_path_plan['planning_summary']}")
        parts.append("\n".join(lines))
    parts.append(f"## 7. Schema context\nUse this as the only source of truth.\n{joined_schema}")
    parts.append(f"## 8. User question\n{question}")
    parts.append("## 9. Final reminder\nReturn exactly one SQL statement ending with a semicolon.")
    return "\n\n".join(parts)


# 兼容少量旧单元测试或外部引用：旧 query_understanding 入口映射到新的 intent_parser。
def query_understanding(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    return intent_parser(state, llm_service, catalog)
