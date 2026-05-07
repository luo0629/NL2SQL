# State Management

> Local and server-state conventions for the current Vue frontend.

---

## Current State Model

There is no Pinia, Vuex, or app-wide store in the current frontend.

All state is local to `frontend/src/App.vue` and stored with `ref` / `computed`.

Key local refs today include:

- input and request state: `prompt`, `status`, `statusMessage`, `errorMessage`
- SQL/result state: `sql`, `notes`, `columns`, `rows`, `params`, `executionSummary`
- debugging state: `debugTrace`

Derived state uses `computed`, for example:

- `loading`
- `visibleRowCount`
- `derivedColumns`
- `hasRenderableRows`
- `formattedParams`
- `formattedDebug`
- `isIdle`

---

## Mapping Rules

Frontend UI state is not identical to backend API state.

- UI `status` is a tone enum: `idle | loading | success | error`
- backend response `status` is a query-mode enum such as `mock | ready | error`

Do not conflate them. The frontend maps backend data into user-facing UI state instead of mirroring it directly.

---

## Server State Convention

The app only keeps the latest query result in memory. There is no persisted history, cache layer, or cross-tab synchronization.

That means new work should assume:

- one active request/result workspace
- overwrite-on-submit behavior
- no shared state across components yet

---

## When a Store Would Actually Be Justified

Only introduce a shared store if the codebase truly grows into one of these cases:

- multiple pages or panels need the same query session
- query history becomes a product feature
- user preferences must survive navigation

Until then, local refs are the project convention.

---

## Forbidden Patterns

- Introducing global state for one-screen ephemeral data without a real multi-consumer need
- Using backend `status` values directly as CSS or UI state without an explicit mapping layer

---

## Common Mistakes

- Treating `columns` as always authoritative even though the UI already supports `derivedColumns` from the first row
- Keeping old result rows visible after a failed or new request because reset logic was skipped
