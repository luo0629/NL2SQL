from typing import cast

from app.agent.state import AgentState
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.utils.exceptions import DangerousSQLError
from app.validator.sql_validator import SQLValidator


def _infer_primary_table(question: str) -> str:
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
    table_name = _infer_primary_table(question)

    if table_name == "sales":
        return (
            "SELECT customer_id, SUM(amount) AS total_revenue\n"
            "FROM sales\n"
            "WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'\n"
            "GROUP BY customer_id\n"
            "ORDER BY total_revenue DESC\n"
            "LIMIT 10;"
        )

    if table_name == "customers":
        return (
            "SELECT id, name, segment, created_at\n"
            "FROM customers\n"
            "WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'\n"
            "ORDER BY created_at DESC\n"
            "LIMIT 20;"
        )

    return (
        "SELECT id, customer_id, total_amount, status, created_at\n"
        "FROM orders\n"
        "WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'\n"
        "ORDER BY created_at DESC\n"
        "LIMIT 20;"
    )


def _extract_text(content: str | list[str | dict[str, str]]) -> str:
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
    cleaned = candidate.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].lstrip()

    if not cleaned.endswith(";"):
        cleaned = f"{cleaned};"

    return cleaned


def _build_prompt(question: str, schema_context: list[str]) -> str:
    joined_schema = "\n".join(f"- {item}" for item in schema_context)
    return (
        "You are a junior-friendly SQL assistant.\n"
        "Return only one read-only SQL query.\n"
        "Only use SELECT or WITH.\n"
        "Do not wrap the SQL in markdown.\n"
        f"Question: {question}\n"
        f"Schema:\n{joined_schema}\n"
    )


def retrieve_schema(state: AgentState, rag_service: RagService) -> AgentState:
    question = state.get("question", "")
    return {"schema_context": rag_service.retrieve_relevant_schema(question)}


def generate_sql(state: AgentState, llm_service: LLMService) -> AgentState:
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
    sql = state.get("sql", "")
    question = state.get("question", "")

    try:
        validator.validate_read_only(sql)
        return {}
    except DangerousSQLError as error:
        return {
            "sql": build_fallback_sql(question),
            "status": "mock",
            "used_fallback": True,
            "validation_errors": [str(error)],
            "explanation": (
                "生成结果没有通过只读安全校验，系统已自动回退到安全的示例 SQL，方便你继续学习整个链路。"
            ),
        }


def finalize_response(state: AgentState) -> AgentState:
    explanation = state.get("explanation", "当前返回的是教学型 SQL 结果。")
    schema_context = state.get("schema_context", [])
    validation_errors = state.get("validation_errors", [])

    if schema_context:
        explanation = (
            f"{explanation} 当前参考的 schema 摘要数量：{len(schema_context)}。"
        )

    if validation_errors:
        explanation = f"{explanation} 最近一次校验问题：{validation_errors[0]}"

    return {"explanation": explanation}
