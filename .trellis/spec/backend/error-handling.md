# Error Handling

> Error types, validation failures, retries, and API behavior for the backend.

---

## Validation and Domain Errors

- `DangerousSQLError` in `app/utils/exceptions.py` is the main safety exception for unsafe SQL.
- `SQLValidator` owns read-only safety checks in `app/validator/sql_validator.py`; the graph performs EXPLAIN preflight after read-only validation.
- Unsafe SQL should surface as validation issues in agent state, not as unstructured router errors.

---

## Graph-Level Failure Handling

The graph does more than generate or reject SQL.

Real flow in `app/agent/graph.py`:

- `intent_parser` feeds `schema_retriever`
- `schema_retriever` feeds `sql_generator`
- `sql_generator` feeds `sql_validator`
- `_should_retry_or_execute(...)` decides between:
  - retry through `sql_generator` when `validation_error` exists and `retry_count < max_retries`
  - continue to `sql_executor` when validation passed
  - short-circuit to `result_formatter` when retries are exhausted
- `sql_executor` always feeds `result_formatter`

This means validation failures are recoverable during generation, but execution failures are formatted as controlled responses rather than retried indefinitely.

---

## Execution Errors

`SQLExecutor.execute()` converts runtime execution failures into structured results:

- timeout → `execution_summary="查询执行超时。"`
- SQLAlchemy exception → `execution_summary="查询执行失败：<ErrorClass>"`

The agent layer then maps those into `NLQueryResponse` fields such as `status`, `execution_summary`, and `error_message`.

---

## LLM Availability and Fallbacks

`LLMService.build_chat_model()` currently supports multiple OpenAI-compatible backends:

- `zhipu`
- `xiaomi`

If the configured provider is unsupported or the selected provider has no API key, the method returns `None`. Upstream nodes treat that as model-unavailable and can fall back to deterministic behavior.

Do not write spec or code that assumes Zhipu is the only real-model path.

---

## HTTP Contract

- FastAPI request validation still returns framework-standard 422 responses.
- Business or execution failures should prefer the `NLQueryResponse` contract from `app/schemas/query.py` over raising raw 500s.
- `status` is currently one of `mock`, `ready`, or `error`.

---

## Forbidden Patterns

- Leaking raw stack traces, credentials, or connection strings to API clients
- Raising generic router exceptions when the graph can return a structured response instead
- Treating all fallback responses as errors; `mock` is a supported contract state

---

## Common Mistakes

- Forgetting that an execution failure may still produce a valid JSON response body with `status="error"`
- Updating response status semantics in `app/schemas/query.py` without aligning frontend handling in `frontend/src/App.vue`
- Treating business semantic override YAML as trusted input instead of validating references against the live schema catalog

---

## Scenario: Business semantic override diagnostics

### 1. Scope / Trigger

- Trigger: optional YAML business semantic overrides can contain stale schema references or unsafe SQL fragments.
- Applies to: `business_semantics.py`, schema catalog construction, debug diagnostics, and prompt rendering.

### 2. Signatures

- Config key: `Settings.business_semantic_yaml_enabled: bool` controls generated YAML override behavior.
- Legacy config key: `Settings.business_semantic_override_path: str | None` is only used when generated YAML behavior is disabled.
- Diagnostics live under `SchemaCatalog.business_semantics.diagnostics` and may be mirrored into agent debug metadata.

### 3. Contracts

- Invalid override entries are diagnostics, not fatal API errors.
- Invalid override entries must not be injected into prompt context as truth.
- Diagnostics must be safe: no absolute local file paths, credentials, raw stack traces, or connection strings.
- Auto-derived semantics remain available when override loading fails.
- Generated YAML content and diagnostics must not include raw `database_url`, credentials, or local absolute YAML paths.

### 4. Validation & Error Matrix

- Missing override file -> safe diagnostic and continue.
- YAML parse error -> safe diagnostic and continue.
- Unknown table/column -> filter entry and continue.
- Unsafe SQL fragment -> filter entry and continue.

### 5. Good/Base/Bad Cases

- Good: stale metric referencing `orders.deleted_column` is filtered and reported in diagnostics.
- Base: no override path configured; diagnostics are empty and auto-derived semantics are used.
- Bad: raw Windows path or raw YAML exception is exposed in API debug output.

### 6. Tests Required

- Missing override path does not expose absolute path.
- Invalid references are filtered.
- Dangerous fragments are filtered.
- Valid overrides survive merge.

### 7. Wrong vs Correct

#### Wrong

```python
business_context.append(raw_override_metric)
```

#### Correct

```python
validated_metric = validate_metric_override(raw_override_metric, catalog)
if validated_metric is not None:
    business_context.append(validated_metric)
```

---

## Scenario: NL2SQL validation retry and controlled execution failure

### 1. Scope / Trigger

- Trigger: generated SQL may fail read-only validation or MySQL `EXPLAIN` before execution.
- Applies to: `sql_validator`, graph routing, `sql_generator` retry context, `sql_executor`, `result_formatter`, and `NLQueryResponse` mapping.

### 2. Signatures

- Response status remains `Literal["mock", "ready", "error"]` unless `app/schemas/query.py` and frontend handling are changed together.
- Retry state fields: `AgentState.validation_error: str`, `AgentState.previous_sql: str`, `AgentState.retry_count: int`, `AgentState.max_retries: int`.
- Execution error state: `AgentState.execution_error: dict[str, object]` for controlled result formatting.
- Final answer field: `AgentState.final_answer: str`, mirrored through `explanation` for the existing API.

### 3. Contracts

- Validation failure is not an HTTP exception; it is fed back into `sql_generator` with `previous_sql` and `validation_error` until retry budget is exhausted.
- `retry_count` must be incremented only on validation failure, not on successful validation or execution failure.
- SQL that fails validation must never reach `sql_executor`.
- Execution failure is not retried in the graph; it is converted by `result_formatter` into a friendly `status="error"` response.
- Never expose raw stack traces, connection URLs, credentials, hostnames, or full driver exception text to clients.

### 4. Validation & Error Matrix

- `DangerousSQLError` -> set `validation_error`, retry SQL generation while `retry_count < max_retries`.
- MySQL `EXPLAIN` error -> set sanitized `validation_error`, retry SQL generation while `retry_count < max_retries`.
- Retry budget exhausted -> route to `result_formatter` with `status="error"` and friendly explanation.
- SQL timeout -> `status="error"`, summary says timeout.
- SQLAlchemy execution error -> `status="error"`, summary includes error class only.

### 5. Good/Base/Bad Cases

- Good: first SQL fails `EXPLAIN`, second SQL uses `validation_error` to repair, validates, then executes once.
- Base: SQL remains invalid for 3 attempts, so no execution occurs and the user receives a clear failure message.
- Bad: invalid SQL reaches `sql_executor`, or validation failures create an infinite graph loop.

### 6. Tests Required

- Graph: validation failure retries generation and passes `previous_sql` + `validation_error` into the next generation attempt.
- Graph: exhausted retry budget routes to `result_formatter` without executor calls.
- Graph: execution failure is sanitized and does not cause repeated database execution.
- Integration: `status="error"` still returns JSON body matching `NLQueryResponse`.

### 7. Wrong vs Correct

#### Wrong

```python
graph_builder.add_edge("sql_validator", "sql_executor")
```

#### Correct

```python
graph_builder.add_conditional_edges(
    "sql_validator",
    _should_retry_or_execute,
    {"sql_generator": "sql_generator", "sql_executor": "sql_executor", "result_formatter": "result_formatter"},
)
```
