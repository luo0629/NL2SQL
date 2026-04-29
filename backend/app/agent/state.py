from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    # 用户输入问题
    question: str
    # RAG 或规则检索出的 schema 摘要
    schema_context: list[str]
    # 最终（或中间）SQL
    sql: str
    # 面向前端展示的解释文本
    explanation: str
    # ready=真实模型路径，mock=回退路径, error=执行失败
    status: Literal["mock", "ready", "error"]
    # 是否使用了 fallback SQL
    used_fallback: bool
    # 安全校验错误列表
    validation_errors: list[str]
    # 执行结果摘要
    execution_summary: str
    # 查询结果行数据
    rows: list[dict[str, Any]]
    # 结果列名
    columns: list[str]
    # 结果总行数
    row_count: int
    # 结果是否被截断
    truncated: bool
    # 执行耗时（毫秒）
    execution_time_ms: float
    # 验证重试次数
    retry_count: int
