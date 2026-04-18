from typing import Literal

from pydantic import BaseModel, Field


class NLQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class NLQueryResponse(BaseModel):
    sql: str
    explanation: str
    status: Literal["mock", "ready"]
