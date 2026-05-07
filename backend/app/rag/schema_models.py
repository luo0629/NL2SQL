from pydantic import BaseModel, Field


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool = False
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    business_terms: list[str] = Field(default_factory=list)
    semantic_role: str | None = None
    sample_values: list[str] = Field(default_factory=list)
    metadata_sources: list[str] = Field(default_factory=list)


class SchemaTable(BaseModel):
    name: str
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    business_terms: list[str] = Field(default_factory=list)
    columns: list[SchemaColumn] = Field(default_factory=list)
    primary_keys: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    searchable_terms: list[str] = Field(default_factory=list)
    metadata_sources: list[str] = Field(default_factory=list)
    missing_metadata: list[str] = Field(default_factory=list)


class SchemaRelation(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relation_type: str | None = None
    confidence: str | None = None
    join_hint: str | None = None


class SchemaCatalog(BaseModel):
    database: str
    tables: list[SchemaTable] = Field(default_factory=list)
    relations: list[SchemaRelation] = Field(default_factory=list)
    synced_at: str | None = None
    database_key: str | None = None
    schema_hash: str | None = None
    version: str | None = None
    metadata_sources: list[str] = Field(default_factory=list)
    missing_metadata: list[str] = Field(default_factory=list)
