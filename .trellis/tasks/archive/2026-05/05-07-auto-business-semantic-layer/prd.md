# Auto-refresh Business Semantic Layer

## Goal

Add a business semantic layer to SQLAgent so natural-language business terms can be mapped reliably to real database schema, while automatically adapting when `database_url` or the live schema changes. The layer must preserve the current six-node NL2SQL pipeline and current LLM configuration, and must not reintroduce the retired SemanticQuery/sql_plan/schema_linking/value_linking/join_path pipeline.

## What I already know

* User wants stable mapping from natural-language business terms to real schema.
* User wants the semantic layer to auto-update/adapt when a new database is connected.
* Current SQLAgent main pipeline is `intent_parser -> schema_retriever -> sql_generator -> sql_validator -> sql_executor -> result_formatter`.
* Retired routes must not be reintroduced: `SemanticQuery`, `sql_plan`, `schema_linking`, `value_linking`, `join_path`.
* Existing reusable foundations: `LLMService`, `RagService`, live schema catalog, `SQLValidator`, `SQLExecutor`.
* Business semantics should use real schema metadata, table/column comments, aliases, metrics, dimensions, enums, and default filters.

## Assumptions (temporary)

* MVP should derive a semantic layer from live schema metadata and comments first, then allow optional project-maintained overrides.
* Semantic refresh should be keyed by `database_url` and schema catalog cache behavior, not by hard-coded database names.
* The semantic layer should enrich `intent_parser`, `schema_retriever`, and `sql_generator`, not create a competing NL2SQL pipeline.

## Open Questions

* Current no blocking questions.

## Requirements

* Add a business semantic layer that maps user business terms to real tables, columns, metrics, dimensions, enums, and default filters.
* Use an automatic + override model: derive base semantics from live schema names/comments/enrichment, then merge optional YAML files for business aliases, metrics, enums, and default filters.
* YAML enablement must be simple: use a boolean true/false setting instead of requiring users to manually provide a YAML file path.
* When YAML is enabled, generated/updated YAML files should live under a fixed project `yaml/` directory, with separate files per database identity so different databases can refresh their own YAML safely.
* Auto-refresh/adapt semantic mappings when `database_url` changes or schema catalog refreshes.
* Use table/column names, comments/descriptions, enum-like comments, and existing schema enrichment as semantic sources.
* Preserve current LLM provider/model configuration.
* Keep the six-node graph main path unchanged in shape; integrate semantics into `intent_parser`, `schema_retriever`, and `sql_generator` context.
* Do not reintroduce retired planning/linking modules.
* Validate generated and override semantic references against the real schema catalog.

## Acceptance Criteria (evolving)

* [ ] Business terms can be matched to real schema tables/columns from live schema metadata.
* [ ] Optional YAML overrides can add aliases, metrics, enums, dimensions, and default filters.
* [ ] YAML override behavior can be enabled/disabled with a true/false setting.
* [ ] YAML files are generated/updated under a fixed `yaml/` directory with database-specific file names.
* [ ] Semantic cache or generation is scoped by `database_url` and refreshable with schema catalog changes.
* [ ] `intent_parser` can use business semantic context to choose more accurate `relevant_tables`.
* [ ] `schema_retriever` and `sql_generator` receive business semantic context alongside schema context.
* [ ] Invalid semantic references are filtered or reported, not injected into SQL prompts as truth.
* [ ] Backend tests cover auto-refresh/cache behavior, override validation, and mapping behavior.

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Backend focused or full tests pass
* API response compatibility considered
* Specs updated if new executable contracts are established
* Rollout/rollback considered because semantic mappings affect SQL generation accuracy

## Technical Approach

* Extend the existing `SchemaCatalog` model with business semantic artifacts instead of creating a competing pipeline.
* Derive automatic semantics during schema catalog construction from table names, column names, table/column comments, existing aliases/business_terms/searchable_terms, semantic roles, and enum-like comments.
* Add a boolean configuration switch for YAML semantics. When enabled, the system resolves a deterministic database-specific YAML file under the fixed project `yaml/` directory.
* Generate or refresh the database-specific YAML file from live schema semantics, then merge it as the editable override source.
* Merge order: live schema-derived semantics first, database-specific YAML overrides second, invalid override references filtered into diagnostics.
* Expose semantic context to current six-node graph through catalog/state fields consumed by `intent_parser`, `schema_retriever`, and `sql_generator` prompts.
* Reuse existing `database_url`-keyed catalog cache so connecting a new database triggers a separate semantic layer automatically.
* Preserve current API response shape unless debug diagnostics are added under existing `debug`.

## Decision (ADR-lite)

**Context**: Raw table/column names and comments are not enough to reliably connect business terms like “sales amount”, “active customer”, or domain-specific enum labels to real schema, especially when switching databases.

**Decision**: Build a business semantic layer using an “automatic + override” model. Automatic semantics come from live schema metadata and refresh with the schema catalog; optional YAML overrides provide business-specific aliases, metrics, dimensions, enums, and default filters.

**Consequences**: New databases get a usable baseline automatically, while teams can improve accuracy without code changes. The trade-off is that override files need schema validation and diagnostics to prevent stale or invalid business rules.

## Out of Scope

* No UI/admin console for editing business semantics in this task.
* No vector database or embedding retrieval in this task.
* No replacement of current LLM provider/model.
* No reintroduction of retired SemanticQuery/sql_plan/linking pipeline.
* No write operations against business databases.
* No automatic training or user feedback learning loop.

## Implementation Plan (small PRs)

* PR1: Add business semantic models, automatic derivation from `SchemaCatalog`, and cache/refresh integration.
* PR2: Add optional YAML override loader with schema validation and diagnostics.
* PR3: Inject semantic context into `intent_parser`, `schema_retriever`, and `sql_generator` prompts without changing graph shape.
* PR4: Add tests for auto derivation, override merge/validation, database_url cache separation, and NL2SQL graph behavior.

## Technical Notes

* Existing semantic metadata already lives partly in `SchemaColumn.semantic_role/business_terms`, `SchemaTable.aliases/business_terms/searchable_terms`, and `SchemaRelation.join_hint`.
* `schema_sync.py` reflects live schema and merges `schema_enrichment.py`; this is the right place to derive/merge business semantics during catalog construction.
* `_get_schema_catalog` in `rag_service.py` caches by `database_url`, supports TTL and `refresh_schema=True`, and is already reused by `graph.py::run_agent` with `schema_sync_timeout_seconds`.
* `RagService.retrieve_relevant_schema()` is not the main graph entry point, so business semantics must be available through the catalog consumed by `agent/nodes.py`.
* Integration points: `_build_intent_prompt`, `_format_table_schema`, `_build_sql_generation_prompt`, and optional `AgentState.semantic_context/semantic_signals`.
* Constraint: current graph must remain six nodes and must not reintroduce `SemanticQuery/sql_plan/schema_linking/value_linking/join_path`.
* Relevant specs: backend database/error/quality guidelines and code reuse/cross-layer guides.
