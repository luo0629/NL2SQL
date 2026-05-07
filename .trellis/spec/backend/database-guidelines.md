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

## Scenario: Design-driven NL2SQL execution with real schema and EXPLAIN

### 1. Scope / Trigger

- Trigger: NL2SQL generated SQL is validated and executed against a real database and can later point at production by changing `Settings.database_url`.
- Applies to: `intent_parser`, `schema_retriever`, `sql_generator`, `sql_validator`, `sql_executor`, `result_formatter`, schema sync, validation, and execution.

### 2. Signatures

- Graph main path: `intent_parser -> schema_retriever -> sql_generator -> sql_validator -> sql_executor -> result_formatter`.
- Intent state fields: `AgentState.intent: str`, `AgentState.relevant_tables: list[str]`.
- Schema state field: `AgentState.schema_context: list[str]`.
- SQL state fields: `AgentState.generated_sql: str`, `AgentState.sql: str`, `AgentState.validation_error: str`, `AgentState.previous_sql: str`, `AgentState.retry_count: int`, `AgentState.max_retries: int`.
- Execution path: `SQLExecutor.execute(sql: str, params: list[object] | None = None, max_rows: int | None = None, timeout_seconds: float | None = None) -> SQLExecutionResult`.
- EXPLAIN path: `SQLExecutor.explain(sql: str, params: list[object] | None = None, timeout_seconds: float | None = None) -> SQLExecutionResult`.

### 3. Contracts

- `intent_parser` must choose candidate tables from the real schema catalog and programmatically drop LLM-returned tables that do not exist.
- `schema_retriever` must render schema context only for `relevant_tables`; relationships are included only when both relation endpoints are selected.
- Schema context should include field name, type, nullable, default, primary key marker, field/table descriptions or comments, and selected-table relations when available.
- `sql_generator` must generate SQL directly from `question`, `intent`, `schema_context`, `previous_sql`, `validation_error`, and `retry_count`; do not route the main path through `SemanticQuery` or `sql_plan` rendering.
- `sql_validator` must run read-only safety checks before any database interaction, then run `EXPLAIN` only for MySQL-compatible URLs.
- Non-MySQL test/development URLs may skip `EXPLAIN` with debug metadata; they must not silently execute invalid SQL before read-only validation.
- Database switching must stay behind `Settings.database_url`; do not branch on hard-coded test database names or table names.

### 4. Validation & Error Matrix

- LLM selects nonexistent table -> filter it out before schema retrieval.
- No relevant table selected -> deterministic fallback chooses a bounded set of real catalog tables.
- Unsafe SQL -> `SQLValidator` rejects before `EXPLAIN` and before execution.
- MySQL `EXPLAIN` failure -> set `validation_error`, increment retry count, and retry generation until `max_retries`.
- Non-MySQL URL -> skip `EXPLAIN` with controlled debug reason, then rely on read-only validation and executor behavior.
- Execution timeout -> structured `SQLExecutionResult` with timeout summary.
- SQLAlchemy runtime error -> structured failure summary with error class only.

### 5. Good/Base/Bad Cases

- Good: LLM picks real tables, schema context includes comments/defaults, generated SELECT passes read-only and MySQL `EXPLAIN`, then executes through `SQLExecutor`.
- Base: LLM unavailable; fallback picks real catalog tables and generates a conservative SELECT over real schema.
- Bad: SQL is generated from a template plan, static hard-coded schema, or `SemanticQuery/sql_plan` main-path rendering.

### 6. Tests Required

- Unit/graph: hallucinated table names are filtered from `relevant_tables`.
- Unit/graph: `schema_retriever` only renders selected tables and selected-table relations.
- Unit/graph: schema context includes default values and comments when available.
- Unit/graph: validation failure retries generation up to `max_retries=3` and never executes failed SQL.
- Unit/integration: high-level query returns `sql`, `rows`, `columns`, `row_count`, and `execution_summary` through the existing API contract.

### 7. Wrong vs Correct

#### Wrong

```python
sql_plan = SQLPlanner().build(query_understanding, schema_linking, value_links, join_path_plan)
generated = SQLGenerator().generate(sql_plan.model_dump())
result = await executor.execute(generated.sql)
```

#### Correct

```python
intent = parse_intent(question, catalog.tables)
schema_context = render_schema_context(catalog, intent.relevant_tables)
sql = generate_sql(question, intent.description, schema_context, previous_sql, validation_error)
validator.validate_read_only(sql)
explain_result = await executor.explain(sql)  # MySQL only
result = await executor.execute(sql)
```
