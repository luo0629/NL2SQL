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


@pytest.mark.anyio
async def test_rag_service_builds_query_schema_plan() -> None:
    service = RagService()

    plan = await service.build_query_schema_plan("查询客户下单状态和用户信息")

    assert plan.schema_context
    assert plan.schema_linking.matched_tables
    assert plan.join_path_plan.primary_table is not None
    assert plan.business_semantic_brief.business_entities
    assert "## Business semantic brief" in plan.business_semantic_brief.prompt_block
    assert plan.business_semantic_brief.join_path_summary
