from collections.abc import Generator
from pathlib import Path
from typing import override

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database.executor import SQLExecutor
from app.dependencies import get_agent_service
from app.main import app
from app.schemas.sql import SQLExecutionResult
from app.services.agent_service import AgentService
from app.services.llm_service import LLMService


class StubLLMService(LLMService):
    @override
    def build_chat_model(self) -> None:
        return None


class StubSQLExecutor(SQLExecutor):
    @override
    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        return SQLExecutionResult(
            rows=[{"id": 1, "name": "mock-row"}],
            row_count=1,
            columns=["id", "name"],
            execution_summary="查询执行成功，共返回 1 行。",
        )


@pytest.fixture(autouse=True)
def isolate_business_semantic_yaml_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None, None, None]:
    monkeypatch.setenv("BUSINESS_SEMANTIC_YAML_DIR", str(tmp_path / "business_semantics_yaml"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        llm_service=StubLLMService(),
        sql_executor=StubSQLExecutor(),
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
