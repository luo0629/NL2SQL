# Type Safety

> TypeScript and response-shape conventions for the frontend.

---

## Current Contract Pattern

`frontend/src/App.vue` declares a local `QueryResponse` type instead of importing a generated shared type.

Real fields handled today include:

- current response fields: `sql`, `params`, `debug`, `explanation`, `status`, `row_count`, `columns`, `rows`, `execution_summary`, `error_message`
- defensive legacy/alternate fields: `notes`, `message`, `detail`, nested `result.sql`, nested `result.notes`

This type is intentionally broader than the backend's exact `NLQueryResponse` contract so the UI can parse older or alternate payloads safely.

---

## Parsing Conventions

The app does not trust raw payloads blindly. It normalizes them with local helper functions:

- `pickText(...)`
- `normalizeNotes(...)`
- `extractBodyMessage(...)`
- `normalizeColumns(...)`
- `normalizeRows(...)`
- `readResponseBody(...)`

When adding new fields from the backend, extend the normalization helpers instead of scattering unchecked casts throughout the template logic.

---

## Alignment Rule

`backend/app/schemas/query.py` is the backend source of truth for the primary API contract.

Whenever `NLQueryResponse` changes:

1. update the local `QueryResponse` type in `frontend/src/App.vue`
2. update the parsing helpers that consume the new field
3. update the UI rendering logic that depends on it

---

## Forbidden Patterns

- Adding direct `as SomeType` assertions for untrusted API payloads without normalization
- Creating multiple competing query-response types in different frontend files once code is split

---

## Common Mistakes

- Assuming `response.ok` implies JSON shape correctness; the current code still normalizes field-by-field after parsing
- Forgetting that `columns` may be empty even when `rows` are present, so callers must preserve the `derivedColumns` fallback
