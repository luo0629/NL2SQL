from typing import Literal

from pydantic import BaseModel, Field


class BusinessSemanticTerm(BaseModel):
    term: str
    kind: str
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class BusinessMetric(BaseModel):
    name: str
    table: str
    column: str
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    expression: str | None = None
    source: Literal["schema", "override"] = "schema"


class BusinessDimension(BaseModel):
    name: str
    table: str
    column: str
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    expression: str | None = None
    source: Literal["schema", "override"] = "schema"


class BusinessEnum(BaseModel):
    name: str
    table: str
    column: str
    values: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    value_aliases: dict[str, list[str]] = Field(default_factory=dict)
    source: Literal["schema", "override"] = "schema"


class BusinessDefaultFilter(BaseModel):
    name: str
    table: str
    condition: str
    columns: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    source: Literal["schema", "override"] = "override"


class BusinessSemanticLayer(BaseModel):
    terms: list[BusinessSemanticTerm] = Field(default_factory=list)
    metrics: list[BusinessMetric] = Field(default_factory=list)
    dimensions: list[BusinessDimension] = Field(default_factory=list)
    enums: list[BusinessEnum] = Field(default_factory=list)
    default_filters: list[BusinessDefaultFilter] = Field(default_factory=list)
    diagnostics: list[dict[str, str]] = Field(default_factory=list)


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool = False
    default: str | None = None
    description: str | None = None
    cross_table_diff: str | None = None
    business_terms: list[str] = Field(default_factory=list)
    semantic_role: str | None = None


class SchemaTable(BaseModel):
    name: str
    database: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    business_terms: list[str] = Field(default_factory=list)
    columns: list[SchemaColumn] = Field(default_factory=list)
    primary_keys: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    searchable_terms: list[str] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.database}.{self.name}" if self.database else self.name


class SchemaRelation(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    from_database: str | None = None
    to_database: str | None = None
    relation_type: str | None = None
    confidence: str | None = None
    join_hint: str | None = None
    ranking_score: float | None = None
    validation_summary: str | None = None

    @property
    def from_qualified_table(self) -> str:
        return f"{self.from_database}.{self.from_table}" if self.from_database else self.from_table

    @property
    def to_qualified_table(self) -> str:
        return f"{self.to_database}.{self.to_table}" if self.to_database else self.to_table


class ColumnGovernanceMetric(BaseModel):
    table: str
    qualified_table: str
    column: str
    quality_score: float
    quality_tier: Literal["high", "medium", "low"]
    deprecated_status: Literal["active", "suspected", "deprecated"] = "active"
    deprecated_reason: str | None = None
    has_description: bool = False
    has_default: bool = False
    nullable: bool = True
    is_primary_key: bool = False
    semantic_role: str | None = None
    signals: list[str] = Field(default_factory=list)


class JoinCoverageMetric(BaseModel):
    table: str
    qualified_table: str
    relation_count: int = 0
    join_candidate_count: int = 0
    covered_join_candidate_count: int = 0
    coverage_ratio: float = 0.0
    covered_join_columns: list[str] = Field(default_factory=list)
    uncovered_join_columns: list[str] = Field(default_factory=list)


class RelationshipGraphNode(BaseModel):
    table: str
    qualified_table: str
    database: str | None = None
    column_count: int = 0
    relation_count: int = 0
    searchable_terms: list[str] = Field(default_factory=list)
    deprecated_column_count: int = 0
    suspected_deprecated_column_count: int = 0


class RelationshipGraphEdge(BaseModel):
    from_table: str
    to_table: str
    from_column: str
    to_column: str
    relation_type: str | None = None
    confidence: str | None = None
    ranking_score: float | None = None
    validation_summary: str | None = None
    governance_tags: list[str] = Field(default_factory=list)


class RelationshipGraphSummary(BaseModel):
    table_count: int = 0
    column_count: int = 0
    relation_count: int = 0
    deprecated_column_count: int = 0
    suspected_deprecated_column_count: int = 0
    avg_join_coverage_ratio: float = 0.0


class RelationshipGraphArtifact(BaseModel):
    version: str = "stage3-mvp-v1"
    database: str
    generated_at: str
    scope_fingerprint: str
    artifact_file: str | None = None
    refresh_trigger: str = "sync_schema_metadata"
    nodes: list[RelationshipGraphNode] = Field(default_factory=list)
    edges: list[RelationshipGraphEdge] = Field(default_factory=list)
    column_quality: list[ColumnGovernanceMetric] = Field(default_factory=list)
    join_coverage: list[JoinCoverageMetric] = Field(default_factory=list)
    summary: RelationshipGraphSummary = Field(default_factory=RelationshipGraphSummary)
    diagnostics: list[dict[str, str]] = Field(default_factory=list)


class SchemaCatalog(BaseModel):
    database: str
    tables: list[SchemaTable] = Field(default_factory=list)
    relations: list[SchemaRelation] = Field(default_factory=list)
    synced_at: str | None = None
    business_semantics: BusinessSemanticLayer | None = None
    relationship_graph: RelationshipGraphArtifact | None = None
