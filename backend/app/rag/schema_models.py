from pydantic import BaseModel, Field


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool = False
    description: str | None = None


class SchemaTable(BaseModel):
    name: str
    description: str | None = None
    columns: list[SchemaColumn] = Field(default_factory=list)
    primary_keys: list[str] = Field(default_factory=list)
    searchable_terms: list[str] = Field(default_factory=list)


class SchemaRelation(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relation_type: str | None = None


class SchemaCatalog(BaseModel):
    database: str
    tables: list[SchemaTable] = Field(default_factory=list)
    relations: list[SchemaRelation] = Field(default_factory=list)
    synced_at: str | None = None
