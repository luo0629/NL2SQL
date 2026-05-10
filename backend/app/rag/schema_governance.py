from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy.engine import make_url

from app.rag.schema_models import (
    ColumnGovernanceMetric,
    JoinCoverageMetric,
    RelationshipGraphArtifact,
    RelationshipGraphEdge,
    RelationshipGraphNode,
    RelationshipGraphSummary,
    SchemaCatalog,
    SchemaColumn,
    SchemaRelation,
    SchemaTable,
)

_EXPLICIT_DEPRECATED_TOKENS = (
    "deprecated",
    "废弃",
    "弃用",
    "停止使用",
    "不再使用",
    "do not use",
    "unused",
    "legacy",
)

_SUSPECTED_DEPRECATED_TOKENS = (
    "保留",
    "备用",
    "临时",
    "历史",
    "old",
    "backup",
    "tmp",
    "temp",
)

_BLOCKED_JOIN_COLUMN_NAMES = {
    "id",
    "tenant_id",
    "deleted",
    "revision",
    "creator",
    "updater",
    "create_user",
    "update_user",
    "create_time",
    "update_time",
    "created_at",
    "updated_at",
    "status",
    "type",
    "name",
    "remark",
}


def _safe_slug(value: str, fallback: str = "database") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return slug[:48] or fallback


def _scope_fingerprint(scope_key: str) -> str:
    return hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:16]


def _artifact_driver_label(scope_key: str) -> str:
    base_url = scope_key.split("|", 1)[0].strip()
    try:
        driver = make_url(base_url).drivername
    except Exception:
        driver = "database"
    return _safe_slug(driver, fallback="database")


def relationship_graph_artifact_path(scope_key: str, artifact_dir: str | Path) -> Path:
    label = _artifact_driver_label(scope_key)
    digest = _scope_fingerprint(scope_key)
    return Path(artifact_dir).expanduser() / f"relationship_graph_{label}_{digest}.json"


def _qualified_table_name(table: SchemaTable) -> str:
    return table.qualified_name


def _deprecation_status(column: SchemaColumn) -> tuple[str, str | None]:
    normalized_name = column.name.strip().lower()
    description = (column.description or "").strip().lower()
    text = f"{normalized_name} {description}"
    if any(token in text for token in _EXPLICIT_DEPRECATED_TOKENS):
        return "deprecated", "explicit_deprecated_marker"
    if normalized_name.startswith(("reserve", "tmp_", "temp_", "bak_", "old_")):
        return "suspected", "name_pattern"
    if any(token in text for token in _SUSPECTED_DEPRECATED_TOKENS):
        return "suspected", "description_or_name_marker"
    return "active", None


def _is_join_candidate(column: SchemaColumn) -> bool:
    normalized_name = column.name.strip().lower()
    role = (column.semantic_role or "").strip().lower()
    deprecated_status, _deprecated_reason = _deprecation_status(column)
    if normalized_name in _BLOCKED_JOIN_COLUMN_NAMES or normalized_name.startswith("reserve"):
        return False
    if role == "internal" or deprecated_status != "active":
        return False
    if column.is_primary_key and normalized_name != "id":
        return True
    if role in {"foreign_key", "identifier", "dimension"}:
        return True
    return normalized_name.endswith(("_id", "_code", "_no"))


def _column_quality_metric(table: SchemaTable, column: SchemaColumn) -> ColumnGovernanceMetric:
    deprecated_status, deprecated_reason = _deprecation_status(column)
    score = 2.0
    signals: list[str] = []

    if column.description:
        score += 3
        signals.append("has_description")
    else:
        signals.append("missing_description")

    if column.semantic_role:
        score += 2
        signals.append(f"semantic_role:{column.semantic_role}")

    if column.business_terms:
        score += 1
        signals.append("has_business_terms")

    if not column.nullable:
        score += 1
        signals.append("not_null")
    else:
        signals.append("nullable")

    if column.default is not None:
        score += 1
        signals.append("has_default")

    if column.is_primary_key:
        score += 1
        signals.append("primary_key")

    if deprecated_status == "suspected":
        score -= 2
        signals.append("suspected_deprecated")
    elif deprecated_status == "deprecated":
        score -= 4
        signals.append("deprecated")

    quality_score = round(min(10.0, max(0.0, score)), 2)
    if quality_score >= 7:
        quality_tier = "high"
    elif quality_score >= 4:
        quality_tier = "medium"
    else:
        quality_tier = "low"

    return ColumnGovernanceMetric(
        table=table.name,
        qualified_table=_qualified_table_name(table),
        column=column.name,
        quality_score=quality_score,
        quality_tier=quality_tier,
        deprecated_status=deprecated_status,
        deprecated_reason=deprecated_reason,
        has_description=bool(column.description),
        has_default=column.default is not None,
        nullable=column.nullable,
        is_primary_key=column.is_primary_key,
        semantic_role=column.semantic_role,
        signals=signals,
    )


def _relation_tag(relation: SchemaRelation, column_quality_by_key: dict[tuple[str, str], ColumnGovernanceMetric]) -> list[str]:
    tags: list[str] = []
    if relation.relation_type:
        tags.append(f"relation_type:{relation.relation_type}")
    if relation.validation_summary:
        tags.append("runtime_validated")
    for key in [
        (relation.from_qualified_table, relation.from_column),
        (relation.to_qualified_table, relation.to_column),
    ]:
        metric = column_quality_by_key.get(key)
        if metric and metric.deprecated_status != "active":
            tags.append(f"{metric.deprecated_status}_endpoint")
    return tags


def _join_coverage_metric(
    table: SchemaTable,
    table_relations: list[SchemaRelation],
    relation_columns: set[str],
) -> JoinCoverageMetric:
    candidate_columns = sorted({column.name for column in table.columns if _is_join_candidate(column)})
    covered_columns = sorted(column for column in candidate_columns if column in relation_columns)
    uncovered_columns = sorted(column for column in candidate_columns if column not in relation_columns)
    coverage_ratio = 0.0 if not candidate_columns else round(len(covered_columns) / len(candidate_columns), 2)
    return JoinCoverageMetric(
        table=table.name,
        qualified_table=_qualified_table_name(table),
        relation_count=len(table_relations),
        join_candidate_count=len(candidate_columns),
        covered_join_candidate_count=len(covered_columns),
        coverage_ratio=coverage_ratio,
        covered_join_columns=covered_columns,
        uncovered_join_columns=uncovered_columns,
    )


def build_relationship_graph_artifact(
    catalog: SchemaCatalog,
    *,
    scope_key: str,
    artifact_dir: str | Path,
    generated_at: str | None = None,
) -> RelationshipGraphArtifact:
    column_quality: list[ColumnGovernanceMetric] = []
    column_quality_by_key: dict[tuple[str, str], ColumnGovernanceMetric] = {}
    for table in catalog.tables:
        qualified_table = _qualified_table_name(table)
        for column in table.columns:
            metric = _column_quality_metric(table, column)
            column_quality.append(metric)
            column_quality_by_key[(qualified_table, column.name)] = metric

    relation_columns_by_table: dict[str, set[str]] = {}
    relation_count_by_table: dict[str, int] = {}
    for relation in catalog.relations:
        for qualified_table, column_name in [
            (relation.from_qualified_table, relation.from_column),
            (relation.to_qualified_table, relation.to_column),
        ]:
            relation_columns_by_table.setdefault(qualified_table, set()).add(column_name)
            relation_count_by_table[qualified_table] = relation_count_by_table.get(qualified_table, 0) + 1

    join_coverage: list[JoinCoverageMetric] = []
    for table in catalog.tables:
        qualified_table = _qualified_table_name(table)
        table_relations = [
            relation
            for relation in catalog.relations
            if relation.from_qualified_table == qualified_table or relation.to_qualified_table == qualified_table
        ]
        join_coverage.append(
            _join_coverage_metric(
                table,
                table_relations,
                relation_columns_by_table.get(qualified_table, set()),
            )
        )

    nodes: list[RelationshipGraphNode] = []
    for table in catalog.tables:
        qualified_table = _qualified_table_name(table)
        table_metrics = [metric for metric in column_quality if metric.qualified_table == qualified_table]
        nodes.append(
            RelationshipGraphNode(
                table=table.name,
                qualified_table=qualified_table,
                database=table.database,
                column_count=len(table.columns),
                relation_count=relation_count_by_table.get(qualified_table, 0),
                searchable_terms=table.searchable_terms,
                deprecated_column_count=sum(1 for metric in table_metrics if metric.deprecated_status == "deprecated"),
                suspected_deprecated_column_count=sum(1 for metric in table_metrics if metric.deprecated_status == "suspected"),
            )
        )

    edges = [
        RelationshipGraphEdge(
            from_table=relation.from_qualified_table,
            to_table=relation.to_qualified_table,
            from_column=relation.from_column,
            to_column=relation.to_column,
            relation_type=relation.relation_type,
            confidence=relation.confidence,
            ranking_score=relation.ranking_score,
            validation_summary=relation.validation_summary,
            governance_tags=_relation_tag(relation, column_quality_by_key),
        )
        for relation in catalog.relations
    ]

    coverage_values = [metric.coverage_ratio for metric in join_coverage]
    summary = RelationshipGraphSummary(
        table_count=len(catalog.tables),
        column_count=sum(len(table.columns) for table in catalog.tables),
        relation_count=len(catalog.relations),
        deprecated_column_count=sum(1 for metric in column_quality if metric.deprecated_status == "deprecated"),
        suspected_deprecated_column_count=sum(1 for metric in column_quality if metric.deprecated_status == "suspected"),
        avg_join_coverage_ratio=round(sum(coverage_values) / len(coverage_values), 2) if coverage_values else 0.0,
    )

    artifact = RelationshipGraphArtifact(
        database=catalog.database,
        generated_at=generated_at or catalog.synced_at or "",
        scope_fingerprint=_scope_fingerprint(scope_key),
        nodes=nodes,
        edges=edges,
        column_quality=column_quality,
        join_coverage=join_coverage,
        summary=summary,
    )

    artifact_path = relationship_graph_artifact_path(scope_key, artifact_dir)
    artifact.artifact_file = artifact_path.name
    try:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        artifact.diagnostics.append(
            {
                "level": "warning",
                "code": "RELATIONSHIP_GRAPH_DIRECTORY_ERROR",
                "message": f"Relationship graph directory could not be prepared: {error.__class__.__name__}",
            }
        )
        return artifact

    try:
        artifact_path.write_text(
            json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as error:
        artifact.diagnostics.append(
            {
                "level": "warning",
                "code": "RELATIONSHIP_GRAPH_WRITE_ERROR",
                "message": f"Relationship graph artifact could not be written: {error.__class__.__name__}",
            }
        )
    return artifact
