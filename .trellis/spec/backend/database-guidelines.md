# Database Guidelines

> Engine usage, query execution, and schema metadata conventions in this project.

---

## Engine and URL

- Async SQLAlchemy engine is created in `app/database/engine.py` from `Settings.database_url`.
- Default local development URL comes from `app/config.py` and is SQLite async.
- MySQL-style URLs are treated specially in engine config so charset/connect args can be applied.

---

## Execution Path

`app/database/executor.py` is the only approved execution path for user-generated SQL.

Real behavior in `SQLExecutor.execute()`:

1. `SQLValidator.validate_read_only(sql)` runs first
2. positional params are remapped to named SQLAlchemy params (`p0`, `p1`, ...)
3. SQL executes through `self.engine.connect()` and `connection.execute(text(sql), sql_params)`
4. optional timeout is enforced with `asyncio.wait_for(...)`
5. result rows are normalized into `SQLExecutionResult`

Do not execute model-generated SQL directly from a router, service, or RAG helper.

---

## Result Shape

`SQLExecutionResult` is the shared execution contract used by the agent layer. The executor returns:

- `rows`: list of JSON-serializable dictionaries
- `row_count`: count after any result cap is applied
- `columns`: ordered result keys
- `truncated`: whether the result was cut by limit
- `execution_summary`: user-facing summary text
- `execution_time_ms`: timing when available

Serialization rules in `SQLExecutor._serialize_value()`:

- `Decimal` → `float`
- `datetime` / `date` / `time` → ISO string
- `bytes` → UTF-8 string when decodable

---

## Limits and Timeouts

- Row cap comes from executor configuration and can be overridden per call with `max_rows`.
- `timeout_seconds` is optional per execution call.
- Timeout and SQLAlchemy failures return a structured `SQLExecutionResult`; they are not allowed to crash the API path by default.

---

## Schema Metadata and RAG

The live schema catalog is built in `app/rag/schema_sync.py` and cached in `app/services/rag_service.py`.

Real pattern from `_get_schema_catalog()`:

- cache key is `settings.database_url`
- TTL comes from `schema_cache_ttl_seconds`
- refresh can bypass cache
- cache access is protected by `_catalog_lock`

`RagService` is responsible for schema retrieval and plan construction, not for running SQL.

---

## Read-Only Policy

- Public query execution is read-only.
- Validator ownership stays in `app/validator/sql_validator.py`.
- Executor re-checks SQL before every execution, even if upstream nodes already validated it.

---

## Forbidden Patterns

- Bypassing `SQLExecutor` for NL2SQL output
- Bypassing `SQLValidator` because a previous graph node already ran validation
- Executing SQL inside `rag/` modules while doing schema retrieval or repair

---

## Common Mistakes

- Assuming parameter order is preserved as anonymous placeholders instead of the executor's `p0`, `p1` named mapping
- Forgetting that cache behavior in `RagService` depends on `database_url`, so tests touching schema metadata should isolate config carefully

---

## Scenario: Auto-refresh business semantic layer

### 1. Scope / Trigger

- Trigger: NL2SQL table and column selection must use business terms derived from live schema metadata and optional project overrides.
- Applies to: `SchemaCatalog`, `business_semantics.py`, schema sync, `RagService` cache behavior, and agent prompt context.

### 2. Signatures

- Config key: `Settings.business_semantic_yaml_enabled: bool` toggles generated YAML override behavior.
- Config key: `Settings.business_semantic_yaml_dir: str` defaults to the project `yaml/` directory for deterministic database-specific YAML files.
- Legacy config key: `Settings.business_semantic_override_path: str | None` is still supported only when YAML generation is disabled.
- Catalog field: `SchemaCatalog.business_semantics` stores derived/merged semantic artifacts and diagnostics.
- Agent state fields: `AgentState.semantic_context: list[str]`, `AgentState.semantic_signals: dict[str, object]`.
- Dependency: `pyyaml` is used only for optional YAML business semantic overrides.

### 3. Contracts

- Business semantics are derived from live `SchemaCatalog` metadata: table/column names, descriptions/comments, aliases, business terms, searchable terms, semantic roles, and enum-like comments.
- Optional YAML overrides may add aliases, metrics, dimensions, enums, value-level enum aliases, and default filters.
- `BusinessEnum.value_aliases` stores prompt-safe conversational aliases grouped by real enum value and remains optional for compatibility with existing generated YAML.
- Schema rendering must expose enum comparison mappings next to the relevant field description, for example `enum_mapping: 待支付/未支付=1, 已支付=2`, so `sql_generator` can see the natural-language phrase and DB value together.
- When YAML is enabled, the system writes/refreshes `business_semantics_<safe-label>_<hash>.yaml` under the configured YAML directory; filenames are derived from `database_url` without exposing credentials.
- YAML files contain refreshed `generated` sections from live schema and user-editable `overrides` sections. Refreshes should preserve `overrides` where practical.
- Override references must be validated against the live catalog. Invalid table names, column names, or unsafe SQL fragments are filtered and recorded as diagnostics.
- Diagnostics may appear in debug metadata, but must not expose local absolute override file paths or secrets.
- Semantic cache follows the existing `database_url`-scoped catalog cache; connecting a new database gets a separate semantic layer automatically.
- Semantics enrich `intent_parser`, `schema_retriever`, and `sql_generator` context without changing the six-node graph shape.

### 4. Validation & Error Matrix

- Override file missing -> continue with auto-derived semantics and safe diagnostic.
- Override YAML parse error -> continue with auto-derived semantics and safe diagnostic.
- Override table not found -> filter that artifact and add diagnostic.
- Override column not found -> filter that artifact and add diagnostic.
- Override SQL fragment contains comments, semicolon, or dangerous keywords -> filter that artifact and add diagnostic.
- Override enum value or alias is non-scalar or contains comments, semicolon, SQL operators, or dangerous keywords -> filter that enum entry or alias and add diagnostic.
- Override SQL fragment references a table/column outside the declared real schema -> filter that artifact and add diagnostic.

### 5. Good/Base/Bad Cases

- Good: a new database with useful comments automatically produces table/column aliases and enum hints; optional YAML adds business metrics.
- Base: no YAML file exists, so the system uses only live schema-derived semantics.
- Bad: stale YAML points to a removed column and the stale reference is injected into the SQL prompt as truth.

### 6. Tests Required

- Unit: auto-derived semantics include terms from table/column names, comments, aliases, and business terms.
- Unit: valid YAML overrides merge into catalog semantics.
- Unit: comment-derived enum mappings and YAML value-level enum aliases render as field-adjacent prompt mappings.
- Unit: invalid override references, unsafe enum values/aliases, and dangerous SQL fragments are filtered into diagnostics.
- Unit: boolean YAML disabled does not create YAML files.
- Unit: YAML enabled creates database-specific files under `yaml/` with no credentials or absolute paths in generated content.
- Unit: valid YAML overrides merge into catalog semantics.
- Unit: missing legacy override file diagnostics do not expose absolute paths.
- Cache: different `database_url` values keep separate semantic layers and YAML files through the existing catalog cache.
- Graph: semantic context is available to intent/schema/sql generation without changing API response fields.

### 7. Wrong vs Correct

#### Wrong

```python
semantic_rules = load_yaml("rules.yaml")
prompt = f"Use these rules as truth: {semantic_rules}"
```

#### Correct

```python
catalog = await _get_schema_catalog()
semantics = build_business_semantics(catalog, override_path=settings.business_semantic_override_path)
# Only validated semantic artifacts are rendered into prompt context.
```

---

## Scenario: Design-driven NL2SQL execution with real schema and EXPLAIN

### 1. Scope / Trigger

- Trigger: NL2SQL generated SQL is validated and executed against a real database and can later point at production by changing `Settings.database_url`.
- Applies to: `intent_parser`, `schema_retriever`, `sql_generator`, `sql_validator`, `sql_executor`, `result_formatter`, schema sync, validation, and execution.

### 2. Signatures

- Graph main path: `load_schema_catalog -> intent_parser -> schema_retriever -> sql_generator -> sql_validator -> value_validator -> sql_executor -> result_formatter`, with retry branches from both validators back to `sql_generator`.
- Intent state fields: `AgentState.intent: str`, `AgentState.relevant_tables: list[str]`.
- Schema state fields: `AgentState.schema_catalog: SchemaCatalog | None`, `AgentState.schema_context: str`.
- Schema relation sources: `schema_sync.sync_schema_metadata()` must build `SchemaCatalog.relations` in this precedence order: live foreign keys -> validated `table_relations.yaml` overrides -> inferred shared-key relations from live schema.
- Schema column enrichment fields: `SchemaColumn.cross_table_diff: str | None` is rendered into `schema_context` when available and is used to warn the generator away from ambiguous join candidates.
- Stage 2 relation fields: `SchemaRelation.ranking_score: float | None` and `SchemaRelation.validation_summary: str | None` may be attached for runtime-validated join candidates.
- Stage 3 graph fields: `SchemaCatalog.relationship_graph: RelationshipGraphArtifact | None` is reserved for offline governance artifacts and future graph-driven retrieval.
- Stage 3 artifact models include: `ColumnGovernanceMetric`, `JoinCoverageMetric`, `RelationshipGraphNode`, `RelationshipGraphEdge`, `RelationshipGraphSummary`, `RelationshipGraphArtifact`.
- Main-path join-priority context: `schema_retriever` may render `Preferred join candidates:` and `Avoid weaker join candidates:` sections derived from relation / governance signals.
- SQL state fields: `AgentState.generated_sql: str`, `AgentState.validation_error: str`, `AgentState.previous_sql: str`, `AgentState.retry_count: int`, `AgentState.max_retries: int`.
- Execution path: `SQLExecutor.execute(sql: str, params: list[object] | None = None, max_rows: int | None = None, timeout_seconds: float | None = None) -> SQLExecutionResult`.
- EXPLAIN path: `SQLExecutor.explain(sql: str, params: list[object] | None = None, timeout_seconds: float | None = None) -> SQLExecutionResult`.
- Runtime probe path: `SQLExecutor.sample_column_values(table: str, column: str, *, order_by: list[str] | None = None, limit: int = 40, timeout_seconds: float | None = None) -> list[object]`.
- Runtime probe settings: `Settings.relation_probe_enabled`, `Settings.relation_probe_top_k`, `Settings.relation_probe_sample_limit`, `Settings.relation_probe_timeout_seconds`.
- Governance artifact settings: `Settings.schema_governance_artifact_dir` controls where relationship graph / governance JSON artifacts are written.
- Agent strategy config: `backend/config/agent_strategy.yaml` is loaded by `app/config_loader.py` as `AppConfig.agent_strategy` and consumed by `app/agent/strategy.py`.
- Agent strategy fields: `AgentRuntimeStrategy.term_sets`, `AgentRuntimeStrategy.join_preferences`, `AgentRuntimeStrategy.fallback`, `AgentRuntimeStrategy.disabled_table_keys`.
- Startup refresh entry: `refresh_startup_schema_artifacts() -> refresh_generated_config_yaml() -> sync_schema_metadata(..., yaml_enabled_override=True)` is the unified startup-time refresh chain for core schema-driven YAML.
- Core startup-refreshed YAML scope: `backend/config/table_relations.yaml`, `backend/config/field_semantics.yaml`, `backend/config/enum_mappings.yaml`, `backend/config/business_terms.yaml`, and scope-isolated `yaml/business_semantics_<scope>.yaml`.

### 3. Contracts

- `intent_parser` must choose candidate tables from the real schema catalog and programmatically drop LLM-returned tables that do not exist.
- `schema_retriever` must render schema context only for `relevant_tables`; relationships are included only when both relation endpoints are selected.
- Schema context should include field name, type, nullable, default, primary key marker, field/table descriptions or comments, and selected-table relations when available.
- When `cross_table_diff` exists for a field, it must be surfaced in `schema_context` so the generator can distinguish business join keys from audit, reserve, deleted, revision, or other same-name fields.
- Live database switching must remain automatic behind `Settings.database_url`; schema relation discovery must not depend on business-table hard-coding in Python constants.
- The default relation strategy is: trust real FK first, allow validated config overrides second, and only then infer shared-key joins from live schema within the same database scope.
- Stage 2 runtime validation must remain metadata-first: probe only a bounded top-K set of plausible candidates after metadata filtering, not every same-name field in the schema.
- Runtime probes must stay read-only, deterministic, and bounded: use `ORDER BY + LIMIT`, sample endpoint columns rather than full join outputs, and keep probe results in relation metadata / `schema_context` / `debug_trace` instead of reshaping the graph contract.
- Even when a table pair has only one shared-key candidate, it should still be eligible for runtime probing if it falls within the configured probe budget; otherwise high-null or low-overlap dirty keys can bypass validation.
- Stage 3 governance artifacts must be generated from `schema_sync.sync_schema_metadata()` after schema, relations, and business semantics are available, but they must remain supporting infrastructure only; the current LangGraph node sequence and API contract stay unchanged.
- Relationship graph artifacts must include at least: node/edge topology, column quality, join coverage, deprecated field status, summary metrics, and safe diagnostics.
- Governance artifact filenames must use a schema-scope fingerprint rather than raw database URLs, and the JSON payload must not leak database passwords or local absolute paths.
- In join coverage metrics, a table with zero join candidates must report `coverage_ratio = 0.0`, not `1.0`.
- Qualified and unqualified table names (for example `jc_experimental.weituo` vs `weituo`) must both resolve against enrichment data for table, column, relation hints, and SQL join-equality comparison.
- Main-path join repair must treat relation ranking / validation / governance signals as executable constraints, not display-only text. When the generated SQL uses a weaker join equality for the same table pair while a stronger alternative exists, the validator path should raise a structured validation error and let the existing retry loop regenerate SQL.
- Table-level disabled keys from `agent_strategy.yaml` are executable constraints, not prompt-only hints. In `nodes.py`-owned SQL generation logic they must be filtered out across schema-context rendering, display-column selection, join candidate preference, weaker-join guidance, and fallback SQL column / ordering selection.
- If a generated JOIN still uses a disabled key, the validator path should return a structured retry message instead of executing the SQL unchanged.
- Missing or malformed `agent_strategy` config must fall back to safe built-in defaults; configuration is allowed to tune behavior but must not be required for baseline execution.
- `sql_generator` must generate SQL directly from `question`, `intent`, `schema_context`, `previous_sql`, `validation_error`, and `retry_count`; do not route the main path through `SemanticQuery` or `sql_plan` rendering.
- `sql_validator` must run read-only safety checks before any database interaction, then run `EXPLAIN` only for MySQL-compatible URLs.
- Non-MySQL test/development URLs may skip `EXPLAIN` with debug metadata; they must not silently execute invalid SQL before read-only validation.
- Database switching must stay behind `Settings.database_url`; do not branch on hard-coded test database names or table names.
- Startup refresh must be schema-driven. Runtime metadata enrichment, table descriptions, enum hints, and startup-generated YAML must not depend on Cangqiong Waimai / `jc_experimental`-specific fallback table names or status mappings as live defaults.
- Startup refresh should preserve user `overrides` while rebuilding `generated` sections from the current schema.
- Startup refresh should avoid rewriting YAML files when the rendered content is unchanged.
- MVP scope only guarantees startup-time refresh unification; runtime watcher behavior may remain separate until explicitly refactored.

### 4. Validation & Error Matrix

- LLM selects nonexistent table -> filter it out before schema retrieval.
- No relevant table selected -> deterministic fallback chooses a bounded set of real catalog tables.
- Live schema has FK metadata -> emit FK relations with highest trust and default `confidence="high"` when no override is present.
- No FK but same-name columns exist -> infer shared-key relations only for non-blocked business fields; exclude reserve, deleted, revision, audit, time, and generic status/type/name/remark fields.
- Candidate relation survives metadata filtering and is in runtime probe top-K -> run bounded endpoint sampling and feed signals back into `ranking_score`, `confidence`, and `validation_summary`.
- Table pair has exactly one shared-key candidate but is within probe budget -> still probe it; do not skip runtime validation just because there is no competing sibling candidate.
- Runtime probe uses `LIMIT` without stable ordering -> reject the design; probes must preserve deterministic `ORDER BY + LIMIT` to stay validator-compatible.
- `schema_sync` finishes successfully -> generate / refresh the relationship graph artifact and attach it to `SchemaCatalog.relationship_graph`.
- Governance artifact path leaks raw DB password or absolute local path -> reject the artifact design; use a fingerprinted filename and safe payload fields only.
- Table has zero join candidates -> `JoinCoverageMetric.coverage_ratio` must be `0.0`.
- Config override uses qualified table names while runtime lookup uses short names -> enrichment lookup must still resolve correctly.
- Generated SQL joins the same table pair on a clearly weaker key while stronger preferred candidates exist -> return a structured validation error and retry generation before execution.
- `agent_strategy.yaml` is missing, empty, or partially malformed -> use built-in default term sets / join weights / fallback parameters and continue safely.
- A table/column is configured under `disabled_table_keys` -> `nodes.py`-owned selection logic should avoid surfacing or preferring that key in schema context, display columns, join candidates, and fallback SQL.
- Generated SQL still joins on a disabled key -> return a structured validation error and retry generation before execution.
- Startup refresh cannot connect to the live database -> log a safe diagnostic, continue application startup, and keep last-known / existing YAML artifacts without crashing the service.
- Startup refresh sees unchanged generated content -> skip file rewrite.
- Live schema has sparse comments or enum hints -> continue with schema-derived best effort output; do not inject sample-database fallback knowledge as truth.
- Unsafe SQL -> `SQLValidator` rejects before `EXPLAIN` and before execution.
- MySQL `EXPLAIN` failure -> set `validation_error`, increment retry count, and retry generation until `max_retries`.
- Non-MySQL URL -> skip `EXPLAIN` with controlled debug reason, then rely on read-only validation and executor behavior.
- Execution timeout -> structured `SQLExecutionResult` with timeout summary.
- SQLAlchemy runtime error -> structured failure summary with error class only.

### 5. Good/Base/Bad Cases

- Good: application startup refreshes the core YAML set from the current schema, preserves `overrides`, refreshes scope-isolated business semantics YAML, skips no-op rewrites, and the NL2SQL path uses live schema metadata rather than sample-database fallback knowledge.
- Base: database metadata is sparse or temporarily unavailable, so startup logs a safe failure/skip diagnostic, keeps existing YAML artifacts, and runtime NL2SQL continues with best-effort live/schema-cached behavior instead of crashing.
- Bad: startup hardcodes Cangqiong Waimai / `jc_experimental` table descriptions or enum hints into live metadata, rewrites YAML on every boot even when unchanged, or fails the whole service just because startup refresh cannot reach the database once.

### 6. Tests Required

- Unit/graph: hallucinated table names are filtered from `relevant_tables`.
- Unit/graph: `schema_retriever` only renders selected tables and selected-table relations.
- Unit/graph: schema context includes default values and comments when available.
- Unit/graph: schema context exposes `cross_table_diff`, relation `confidence`, and relation `hint` when present.
- Unit/graph: schema context can surface runtime validation summaries / ranking signals without changing the graph shape.
- Unit/schema: qualified and unqualified table names both resolve against table/column/relation enrichment.
- Unit/schema: inferred shared-key relation generation includes business-key fields and excludes audit/reserve/time/status-like fields.
- Unit/schema: runtime probes can promote higher-overlap business keys over weaker candidates and must respect `relation_probe_top_k` budget.
- Unit/schema: single-candidate table pairs within budget still receive bounded runtime probing.
- Unit/executor: `sample_column_values()` enforces bounded, deterministic read-only sampling.
- Unit/governance: `schema_sync` attaches `relationship_graph` and writes a safe artifact file under `schema_governance_artifact_dir`.
- Unit/governance: artifact JSON includes `artifact_file` self-description, summary metrics, column quality, join coverage, deprecated status, and diagnostics.
- Unit/governance: zero-candidate tables produce `coverage_ratio == 0.0`.
- Unit/graph: schema context exposes `Preferred join candidates` and `Avoid weaker join candidates` when stronger/weaker alternatives coexist.
- Unit/graph: qualified catalog relations still block weaker unqualified SQL joins for the same table pair.
- Unit/graph: table-level disabled keys are excluded from schema context rendering, preferred output-column selection, join candidate preference, and fallback SQL generation.
- Unit/config: `agent_strategy.yaml` overrides are merged by `AppConfig.agent_strategy`; missing or malformed values fall back to built-in defaults.
- Unit/graph: weaker join selection yields a structured validation error, increments retry, preserves `previous_sql`, and prevents execution until a stronger alternative is chosen.
- Unit/graph: disabled-key join selection yields a structured validation error, increments retry, preserves `previous_sql`, and prevents execution until a non-disabled key is chosen.
- Unit/graph: validation failure retries generation up to `max_retries=3` and never executes failed SQL.
- Unit/startup: `lifespan` awaits the unified startup refresh entry exactly once and still starts successfully when refresh raises.
- Unit/config-generation: startup refresh updates the 4 core `backend/config` YAML files, preserves `overrides`, and skips rewriting unchanged content.
- Unit/business-semantics: startup-linked schema sync refreshes `business_semantics_<scope>.yaml`, preserves `overrides`, and keeps scope-specific filenames.
- Unit/schema-sync: live table descriptions and enum hints come from current schema/comments or validated overrides, not from Cangqiong/jc_experimental fallback constants.
- Unit/integration: high-level query returns `sql`, `rows`, `columns`, `row_count`, and `execution_summary` through the existing API contract.

### 7. Wrong vs Correct

#### Wrong

```python
schema_context += "\nPreferred join candidates: ..."
# Still allow any join equality through to execution.
```

#### Correct

```python
message = _best_alternative_join_message(sql, catalog)
if message:
    raise DangerousSQLError(message)
```

#### Wrong

```python
# Key is marked unusable in governance config, but nodes.py still keeps it
# in display-column ranking, join preference, or fallback SQL ordering.
```

#### Correct

```python
strategy = get_agent_runtime_strategy()
disabled_keys = strategy.disabled_keys_for(table_name)
# Filter disabled keys before rendering schema context, ranking join candidates,
# or building fallback SQL.
```

#### Wrong

```python
if relation.from_qualified_table == left_table and relation.to_qualified_table == right_table:
    ...
```

#### Correct

```python
if _join_relation_pair_matches_tables(relation, left_table, right_table):
    ...  # qualified/unqualified variants normalize to the same pair
```

#### Wrong

```python
# value_validation imports sqlglot, but pyproject.toml does not declare it
```

#### Correct

```toml
[project]
dependencies = [
  ...,
  "sqlglot>=27.11.0",
]
```

#### Wrong

```python
# Boot refreshes backend/config YAML, but semantic YAML is only refreshed later
# on first request, so startup artifacts are inconsistent.
await refresh_generated_config_yaml()
```

#### Correct

```python
await refresh_startup_schema_artifacts()
# This unified entry refreshes core backend/config YAML first and then
# refreshes scope-isolated business semantics YAML from the same startup path.
```
