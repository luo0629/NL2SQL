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
