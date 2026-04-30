import pytest

from app.config import get_settings
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable
from app.services import rag_service
from app.services.rag_service import RagService


@pytest.mark.anyio
async def test_rag_service_schema_catalog_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("SCHEMA_CACHE_TTL_SECONDS", "300")
    get_settings.cache_clear()

    calls = {"count": 0}

    async def fake_sync_schema_metadata() -> SchemaCatalog:
        calls["count"] += 1
        return SchemaCatalog(
            database="test",
            tables=[
                SchemaTable(
                    name="dish",
                    description="菜品主表",
                    columns=[
                        SchemaColumn(
                            name="status",
                            data_type="int",
                            nullable=True,
                            description="0 停售 1 起售",
                        )
                    ],
                    primary_keys=["id"],
                    searchable_terms=["dish", "菜品", "status", "起售", "停售"],
                )
            ],
            relations=[],
            synced_at="now",
        )

    monkeypatch.setattr(rag_service, "sync_schema_metadata", fake_sync_schema_metadata)
    rag_service._catalog_cache = None
    rag_service._catalog_cached_at = None

    service = RagService()
    await service.retrieve_relevant_schema("查询起售的菜品")
    await service.build_query_schema_plan("查询停售的菜品")

    assert calls["count"] == 1

