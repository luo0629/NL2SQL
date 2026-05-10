from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.rag.schema_governance import build_relationship_graph_artifact, relationship_graph_artifact_path
from app.rag.schema_introspection import LiveSchemaSnapshot, SchemaInspection
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable
from app.rag.schema_sync import sync_schema_metadata


def test_relationship_graph_artifact_contains_column_quality_join_coverage_and_safe_file_output(tmp_path) -> None:
    catalog = SchemaCatalog(
        database="testdb",
        tables=[
            SchemaTable(
                name="orders",
                database="testdb",
                searchable_terms=["orders", "订单"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                    SchemaColumn(name="legacy_code", data_type="VARCHAR", nullable=True, description="废弃旧字段"),
                ],
            ),
            SchemaTable(
                name="payments",
                database="testdb",
                searchable_terms=["payments", "支付"],
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="order_no", data_type="VARCHAR", nullable=False, description="订单编号", semantic_role="dimension"),
                ],
            ),
        ],
        relations=[
            SchemaRelation(
                from_database="testdb",
                from_table="payments",
                from_column="order_no",
                to_database="testdb",
                to_table="orders",
                to_column="order_no",
                relation_type="inferred-shared-key",
                confidence="high",
                ranking_score=9.5,
                validation_summary="sample_probe(rows=4/4; non_null=1.00/1.00; distinct=1.00/1.00; overlap=0.75)",
            )
        ],
        synced_at="2026-05-10T00:00:00+00:00",
    )

    artifact = build_relationship_graph_artifact(
        catalog,
        scope_key="mysql+asyncmy://user:***@localhost:3306/testdb|databases=testdb|tables=",
        artifact_dir=tmp_path,
        generated_at=catalog.synced_at,
    )

    assert artifact.artifact_file is not None
    assert artifact.summary.table_count == 2
    assert artifact.summary.relation_count == 1
    assert any(metric.column == "legacy_code" and metric.deprecated_status == "deprecated" for metric in artifact.column_quality)

    coverage = next(metric for metric in artifact.join_coverage if metric.qualified_table == "testdb.orders")
    assert coverage.join_candidate_count == 1
    assert coverage.covered_join_columns == ["order_no"]
    assert coverage.coverage_ratio == 1.0

    edge = artifact.edges[0]
    assert "runtime_validated" in edge.governance_tags
    assert "relation_type:inferred-shared-key" in edge.governance_tags

    artifact_path = tmp_path / artifact.artifact_file
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["artifact_file"] == artifact.artifact_file
    assert payload["summary"]["deprecated_column_count"] == 1
    assert "pass" not in artifact_path.name.lower()
    assert "pass" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.anyio
async def test_sync_schema_metadata_generates_relationship_graph_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost:3306/testdb")
    monkeypatch.setenv("DATABASE_NAMES", "testdb")
    monkeypatch.setenv("RELATION_PROBE_ENABLED", "false")
    monkeypatch.setenv("SCHEMA_GOVERNANCE_ARTIFACT_DIR", str(tmp_path))
    get_settings.cache_clear()

    snapshot = LiveSchemaSnapshot(
        default_database="testdb",
        configured_databases=["testdb"],
        supports_named_schemas=True,
        expose_table_database=True,
        inspections={
            "testdb": SchemaInspection(
                table_names=["orders", "payments"],
                columns_by_table={
                    "orders": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "订单ID"},
                        {"name": "order_no", "type": "varchar", "nullable": False, "default": None, "comment": "订单编号"},
                        {"name": "legacy_code", "type": "varchar", "nullable": True, "default": None, "comment": "deprecated field"},
                    ],
                    "payments": [
                        {"name": "id", "type": "int", "nullable": False, "default": None, "comment": "支付ID"},
                        {"name": "order_no", "type": "varchar", "nullable": False, "default": None, "comment": "订单编号"},
                    ],
                },
                primary_keys_by_table={"orders": ["id"], "payments": ["id"]},
                foreign_keys=[
                    {
                        "from_database": "testdb",
                        "from_table": "payments",
                        "from_column": "order_no",
                        "to_database": "testdb",
                        "to_table": "orders",
                        "to_column": "order_no",
                    }
                ],
                indexes_by_table={"orders": [], "payments": []},
                comments_by_table={"orders": "订单主表", "payments": "支付表"},
            )
        },
    )

    async def fake_inspect_live_schema() -> LiveSchemaSnapshot:
        return snapshot

    monkeypatch.setattr("app.rag.schema_sync.inspect_live_schema", fake_inspect_live_schema)
    monkeypatch.setattr(
        "app.rag.schema_sync.load_schema_enrichment",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.rag.schema_sync.load_value_mappings",
        lambda: {},
    )
    monkeypatch.setattr(
        "app.rag.schema_sync.get_fallback_mapping_for_column",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.rag.schema_sync.merge_column_description",
        lambda db_description, fallback_mapping: db_description,
    )
    monkeypatch.setattr(
        "app.rag.schema_sync.get_table_enrichment",
        lambda *args, **kwargs: SimpleNamespace(aliases=[], business_terms=[]),
    )
    monkeypatch.setattr(
        "app.rag.schema_sync.get_column_enrichment",
        lambda *args, **kwargs: SimpleNamespace(cross_table_diff=None, business_terms=[], semantic_role=None),
    )
    monkeypatch.setattr(
        "app.rag.schema_sync.get_relation_enrichment",
        lambda *args, **kwargs: SimpleNamespace(confidence=None, join_hint=None),
    )
    monkeypatch.setattr(
        "app.rag.schema_sync.attach_business_semantics",
        lambda catalog, *args, **kwargs: catalog,
    )
    monkeypatch.setattr(
        "app.config_loader.get_app_config",
        lambda: SimpleNamespace(table_relations={"relations": []}),
    )

    catalog = await sync_schema_metadata()

    assert catalog.relationship_graph is not None
    assert catalog.relationship_graph.summary.table_count == 2
    assert any(
        metric.column == "legacy_code" and metric.deprecated_status == "deprecated"
        for metric in catalog.relationship_graph.column_quality
    )
    assert any(metric.coverage_ratio == 1.0 for metric in catalog.relationship_graph.join_coverage)

    scope_key = get_settings().schema_scope_key
    artifact_path = relationship_graph_artifact_path(scope_key, tmp_path)
    assert artifact_path.exists()
    content = artifact_path.read_text(encoding="utf-8")
    assert "pass@" not in content
    assert str(tmp_path) not in content


def test_relationship_graph_join_coverage_is_zero_when_table_has_no_join_candidates(tmp_path) -> None:
    catalog = SchemaCatalog(
        database="testdb",
        tables=[
            SchemaTable(
                name="audit_logs",
                database="testdb",
                columns=[
                    SchemaColumn(name="id", data_type="INTEGER", nullable=False, is_primary_key=True),
                    SchemaColumn(name="status", data_type="VARCHAR", nullable=True),
                    SchemaColumn(name="remark", data_type="VARCHAR", nullable=True),
                ],
            )
        ],
        relations=[],
        synced_at="2026-05-10T00:00:00+00:00",
    )

    artifact = build_relationship_graph_artifact(
        catalog,
        scope_key="sqlite+aiosqlite:///./nl2sql.db|databases=testdb|tables=",
        artifact_dir=tmp_path,
        generated_at=catalog.synced_at,
    )

    coverage = artifact.join_coverage[0]
    assert coverage.join_candidate_count == 0
    assert coverage.coverage_ratio == 0.0
    assert artifact.summary.avg_join_coverage_ratio == 0.0
