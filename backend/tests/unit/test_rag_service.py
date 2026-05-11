import pytest

from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable
from app.services import rag_service
from app.services.rag_service import RagService


@pytest.mark.anyio
async def test_rag_service_returns_real_schema_context(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = SchemaCatalog(
        database="sales",
        tables=[
            SchemaTable(
                name="orders",
                description="订单表",
                columns=[
                    SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True),
                    SchemaColumn(name="customer_id", data_type="int", nullable=False, semantic_role="foreign_key"),
                ],
            ),
            SchemaTable(
                name="customers",
                description="客户表",
                columns=[
                    SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="varchar", nullable=True, semantic_role="dimension"),
                ],
            ),
        ],
        relations=[
            SchemaRelation(
                from_table="orders",
                from_column="customer_id",
                to_table="customers",
                to_column="id",
                relation_type="foreign_key",
            )
        ],
        synced_at="now",
    )
    monkeypatch.setattr(rag_service, "_get_schema_catalog", lambda refresh=False: __import__("asyncio").sleep(0, result=catalog))
    service = RagService()

    context = await service.retrieve_relevant_schema("查询客户订单信息")

    joined = "\n".join(context)
    assert "table orders" in joined or "table customers" in joined
    assert "schema-hit:" not in joined
    assert "relations" in joined or "table" in joined
    assert "description:" in joined


@pytest.mark.anyio
async def test_rag_service_limits_context_to_requested_real_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = SchemaCatalog(
        database="sales",
        tables=[
            SchemaTable(name="orders", description="订单表", columns=[SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True)]),
            SchemaTable(name="customers", description="客户表", columns=[SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True)]),
        ],
        relations=[],
        synced_at="now",
    )
    monkeypatch.setattr(rag_service, "_get_schema_catalog", lambda refresh=False: __import__("asyncio").sleep(0, result=catalog))
    service = RagService()

    context = await service.retrieve_relevant_schema(
        "查询订单",
        relevant_tables=["orders", "missing_table"],
    )

    joined = "\n".join(context)
    assert "table orders" in joined
    assert "missing_table" not in joined
