from typing import Literal

from pydantic import BaseModel, Field


class NLQueryRequest(BaseModel):
    # 用户输入问题：限制长度避免异常长 prompt。
    question: str = Field(min_length=1, max_length=500)


class NLQueryResponse(BaseModel):
    # 生成出的 SQL 语句
    sql: str
    # 对 SQL 结果来源与处理过程的说明
    explanation: str
    # ready=真实模型生成，mock=回退策略生成, error=执行失败
    status: Literal["mock", "ready", "error"]
    # 结构化查询结果
    rows: list[dict[str, object]] = Field(default_factory=list)
    # 结果行数
    row_count: int = 0
    # 返回结果列信息
    columns: list[str] = Field(default_factory=list)
    # 执行摘要，例如空结果或截断说明
    execution_summary: str | None = None
    # 执行失败时的错误信息
    error_message: str | None = None
    # 执行耗时（毫秒）
    execution_time_ms: float | None = None
