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
    rag_service._catalog_cache.clear()
    rag_service._catalog_cached_at.clear()

    service = RagService()
    await service.retrieve_relevant_schema("查询起售的菜品")
    await service.build_query_schema_plan("查询停售的菜品")

    assert calls["count"] == 1


@pytest.mark.anyio
async def test_rag_service_schema_catalog_cache_is_keyed_by_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("SCHEMA_CACHE_TTL_SECONDS", "300")

    calls: list[str] = []

    async def fake_sync_schema_metadata() -> SchemaCatalog:
        database_url = get_settings().database_url
        calls.append(database_url)
        return SchemaCatalog(database=database_url, tables=[], relations=[], synced_at="now")

    monkeypatch.setattr(rag_service, "sync_schema_metadata", fake_sync_schema_metadata)
    rag_service._catalog_cache.clear()
    rag_service._catalog_cached_at.clear()

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./first.db")
    get_settings.cache_clear()
    await RagService().retrieve_relevant_schema("查询菜品")

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./second.db")
    get_settings.cache_clear()
    await RagService().retrieve_relevant_schema("查询菜品")

    assert calls == [
        "sqlite+aiosqlite:///./first.db",
        "sqlite+aiosqlite:///./second.db",
    ]


@pytest.mark.anyio
async def test_rag_service_schema_catalog_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("SCHEMA_CACHE_TTL_SECONDS", "300")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./refresh.db")
    get_settings.cache_clear()

    calls = {"count": 0}

    async def fake_sync_schema_metadata() -> SchemaCatalog:
        calls["count"] += 1
        return SchemaCatalog(database="refresh", tables=[], relations=[], synced_at=str(calls["count"]))

    monkeypatch.setattr(rag_service, "sync_schema_metadata", fake_sync_schema_metadata)
    rag_service._catalog_cache.clear()
    rag_service._catalog_cached_at.clear()

    service = RagService()
    await service.build_query_schema_plan("查询菜品")
    await service.build_query_schema_plan("查询菜品", refresh_schema=True)

    assert calls["count"] == 2

