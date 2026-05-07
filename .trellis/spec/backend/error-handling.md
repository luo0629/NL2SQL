# Error Handling

> Error types, validation failures, retries, and API behavior for the backend.

---

## Validation and Domain Errors

- `DangerousSQLError` in `app/utils/exceptions.py` is the main safety exception for unsafe SQL.
- `SQLValidator` owns read-only checks plus plan provenance / plan-match validation in `app/validator/sql_validator.py`.
- Unsafe SQL should surface as validation issues in agent state, not as unstructured router errors.

---

## Graph-Level Failure Handling

The graph does more than generate or reject SQL.

Real flow in `app/agent/graph.py`:

- `generate_sql` feeds `validate_sql`
- `_should_retry_or_fallback(...)` decides between:
  - retry through `sql_repairing`
  - continue to `execute_sql`
  - short-circuit to `finalize_response`
- `sql_repairing` loops back into `generate_sql`

This means validation failures may be recoverable and should not automatically be modeled as terminal HTTP failures.

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

---

## Scenario: NL2SQL execution gate and repair failure

### 1. Scope / Trigger

- Trigger: a generated SQL statement may be skipped, executed, repaired, or failed inside the agent graph.
- Applies to: `validate_sql`, `sql_repairing`, `execute_sql`, graph routing, and `NLQueryResponse` mapping.

### 2. Signatures

- Response status remains `Literal["mock", "ready", "error"]` unless `app/schemas/query.py` and frontend handling are changed together.
- Debug gate shape: `debug.execution_gate.allowed: bool`, `debug.execution_gate.reasons: list[str]`.
- Execution error state: `AgentState.execution_error: dict[str, object]` for controlled retry/failure decisions.

### 3. Contracts

- Low-confidence SQL is not an HTTP exception and not necessarily `status="error"`; it is a controlled non-execution response with an explanation.
- Runtime SQL failure should be converted to `status="error"` with sanitized `execution_summary` and optional repair attempt metadata.
- If repair cannot safely produce a new plan, the graph must route to `finalize_response` instead of generating and executing again.
- Never expose raw stack traces, connection URLs, credentials, hostnames, or full driver exception text to clients.

### 4. Validation & Error Matrix

- `execution_gate.allowed=false` -> skip executor, return explanation/clarification.
- `DangerousSQLError` -> validation issue, repair only if marked repairable.
- SQL timeout -> `status="error"`, summary says timeout.
- SQLAlchemy error -> `status="error"`, summary includes error class only.
- Repair node returns terminal error -> route directly to `finalize_response`.

### 5. Good/Base/Bad Cases

- Good: failed execution is attempted once for controlled repair, then either succeeds or returns sanitized failure.
- Base: model unavailable and deterministic repair cannot fix the failure, so the response fails cleanly without re-querying.
- Bad: graph loops from terminal repair failure back into `generate_sql`, causing repeated database calls.

### 6. Tests Required

- Graph: execution failure with no safe repair calls the executor only once.
- Graph: sanitized error summary does not contain `database_url` or raw connection details.
- Integration: `status="error"` still returns JSON body matching `NLQueryResponse`.
- Frontend/build: `status="error"` is displayed as failure even when HTTP status is 200.

### 7. Wrong vs Correct

#### Wrong

```python
graph_builder.add_edge("sql_repairing", "generate_sql")
```

#### Correct

```python
graph_builder.add_conditional_edges(
    "sql_repairing",
    _after_sql_repairing,
    {"generate_sql": "generate_sql", "finalize_response": "finalize_response"},
)
```
