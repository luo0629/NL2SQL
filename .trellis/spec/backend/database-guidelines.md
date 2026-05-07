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

## Scenario: SemanticQuery-gated NL2SQL execution

### 1. Scope / Trigger

- Trigger: NL2SQL generated SQL is executed against a real database and can later point at production by changing `Settings.database_url`.
- Applies to: agent graph nodes, semantic query construction, SQL planning/generation, validation, and execution.

### 2. Signatures

- Semantic state field: `AgentState.semantic_query: dict[str, object]`
- Gate state field: `AgentState.execution_gate: {"allowed": bool, "confidence": float, "threshold": float, "reasons": list[str]}`
- Execution path: `SQLExecutor.execute(sql: str, params: list[object] | None = None, max_rows: int | None = None, timeout_seconds: float | None = None) -> SQLExecutionResult`

### 3. Contracts

- `semantic_query` is the primary NL2SQL semantic contract. It should carry intent, entities, metrics, dimensions, filters, time range, joins, order_by, limit, confidence, and clarification prompts.
- `sql_plan` is a compatibility/debug/rendering layer derived from semantic input; do not treat it as the user-intent source of truth.
- `execution_gate.allowed=false` means the SQL draft must not call `SQLExecutor`; response should explain why execution was skipped.
- Database switching must stay behind `Settings.database_url`; do not branch logic on a hard-coded test database name.

### 4. Validation & Error Matrix

- Unsafe SQL -> `SQLValidator` rejects before executor runs.
- Low semantic/join/schema confidence -> `execution_gate.allowed=false`, no database call.
- Execution timeout -> structured `SQLExecutionResult` with timeout summary.
- SQLAlchemy runtime error -> structured failure summary with error class only.

### 5. Good/Base/Bad Cases

- Good: high-confidence schema-grounded query executes through `SQLExecutor` and returns rows, columns, row_count, and execution_summary.
- Base: low-confidence query returns SQL draft plus clarification/explanation and does not query the database.
- Bad: generated SQL is executed directly in a router, service, RAG helper, or frontend confirmation path.

### 6. Tests Required

- Unit: SemanticQuery builder computes confidence and clarification prompts.
- Unit: SQLPlanner consumes `semantic_query.filters` as the WHERE source while preserving parameterized `:pN` placeholders.
- Graph/integration: low-confidence state does not call executor.
- Graph/integration: high-confidence state executes and returns normalized result fields.

### 7. Wrong vs Correct

#### Wrong

```python
sql_plan = SQLPlanner().build(query_understanding, schema_linking, value_links, join_path_plan)
result = await executor.execute(SQLGenerator().generate(sql_plan).sql)
```

#### Correct

```python
semantic_query = SemanticQueryBuilder().build(query_understanding, schema_linking, value_links, join_path_plan, brief)
execution_gate = semantic_query.execution_gate()
sql_plan = SQLPlanner().build(semantic_query=semantic_query.model_dump(), value_links=value_links)
if execution_gate["allowed"]:
    result = await executor.execute(SQLGenerator().generate(sql_plan.model_dump()).sql)
```
