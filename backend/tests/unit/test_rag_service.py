import pytest

from app.services.rag_service import RagService


@pytest.mark.anyio
async def test_rag_service_returns_real_schema_context() -> None:
    service = RagService()

    context = await service.retrieve_relevant_schema("查询菜品分类和菜品信息")

    joined = "\n".join(context)
    assert "table dish" in joined or "table category" in joined
    assert "schema-hit:" not in joined
    assert "relations" in joined or "table" in joined
    assert "description:" in joined
