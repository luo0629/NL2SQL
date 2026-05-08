import pytest

from app.services.rag_service import RagService


@pytest.mark.anyio
async def test_rag_service_returns_real_schema_context() -> None:
    service = RagService()

    context = await service.retrieve_relevant_schema("查询委托结算信息")

    joined = "\n".join(context)
    assert "table jc_experimental.weituo" in joined or "table jc_experimental.weituo_clearing_detail" in joined
    assert "schema-hit:" not in joined
    assert "relations" in joined or "table" in joined
    assert "description:" in joined


@pytest.mark.anyio
async def test_rag_service_limits_context_to_requested_real_tables() -> None:
    service = RagService()

    context = await service.retrieve_relevant_schema(
        "查询委托",
        relevant_tables=["jc_experimental.weituo", "missing_table"],
    )

    joined = "\n".join(context)
    assert "table jc_experimental.weituo" in joined
    assert "missing_table" not in joined
