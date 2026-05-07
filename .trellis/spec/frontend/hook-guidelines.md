# Hook Guidelines

> Composables and request-flow conventions for the current Vue frontend.

---

## Current State

There are no reusable composables yet.

Real behavior today:

- request logic lives directly in `frontend/src/App.vue`
- helpers such as `pickText`, `normalizeNotes`, `normalizeColumns`, `normalizeRows`, `renderCell`, and `readResponseBody` are plain local functions inside the same SFC
- async work is triggered by `handleSubmit()`

When documenting or extending the current app, start from that single-file pattern instead of assuming a shared `useQuery` abstraction already exists.

---

## Request Flow Convention

`handleSubmit()` in `App.vue` is the reference flow:

1. trim and validate `prompt`
2. set loading and clear stale state
3. `fetch('/api/query', { method: 'POST', body: JSON.stringify({ question }) })`
4. parse JSON-or-text response through `readResponseBody()`
5. branch on `response.ok`
6. normalize SQL, notes, columns, rows, params, debug payload, and summaries into local refs

Any future composable should preserve this behavior unless the product contract intentionally changes.

---

## If a Composable Is Introduced Later

If the app grows enough to extract one, keep the same responsibilities together:

- HTTP call
- body parsing
- error normalization
- clearing stale result state between submissions
- mapping backend response fields into frontend refs

---

## Forbidden Patterns

- Duplicating `fetch('/api/query')` flows in multiple places without first extracting the shared normalization logic
- Splitting parsing helpers across files while leaving slightly different field assumptions in each caller

---

## Common Mistakes

- Forgetting to clear `rows`, `columns`, `params`, or `debugTrace` before a new submission and accidentally showing stale data
- Treating request errors and empty-result success as the same UI state
