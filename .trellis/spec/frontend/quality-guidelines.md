# Quality Guidelines

> Code standards and tooling for the current Vue frontend.

---

## Tooling

- Package manager: `pnpm`
- Local dev: `pnpm dev`
- Production build check: `pnpm build`
- Preview build: `pnpm preview`

There is currently no frontend test runner configured in `frontend/package.json`.

---

## Framework and Style

- Vue 3 with `<script setup lang="ts">`
- Composition API with `ref` and `computed`
- TypeScript is part of the build via `vue-tsc -b && vite build`

Keep new code aligned with this stack; do not introduce Options API or extra state libraries casually.

---

## Build-Time Safety

The main enforced frontend quality gate right now is successful type-check + build through `pnpm build`.

If a change touches API response handling, also manually verify:

- loading state
- backend error display
- empty-result display
- tabular result rendering
- optional debug rendering

---

## Forbidden Patterns

- Switching package managers for routine project work
- Adding frontend conventions that assume Vitest, ESLint, or component-test infrastructure already exists when it does not

---

## Common Mistakes

- Treating `pnpm dev` success as enough when only `pnpm build` actually runs `vue-tsc`
- Forgetting the frontend depends on the backend proxy target being reachable at port `8787` during local dev

---

## Scenario: NL2SQL gated execution response handling

### 1. Scope / Trigger

- Trigger: backend `POST /api/query` may return generated SQL that was not executed because confidence was too low, or may return `status="error"` in an HTTP 200 JSON body.
- Applies to: frontend API response typing, result status display, empty-state display, and debug rendering.

### 2. Signatures

- Backend response type remains `NLQueryResponse` with `status: "mock" | "ready" | "error"`.
- Optional debug contract: `debug.execution_gate.allowed?: boolean`, `debug.execution_gate.reasons?: string[]`.
- Result fields: `rows`, `columns`, `row_count`, `execution_summary`, `error_message`, `debug`.

### 3. Contracts

- `status="error"` must render as a failed query state even when the HTTP response is 200.
- `debug.execution_gate.allowed === false` means the backend intentionally skipped database execution; do not show it as a successful empty result.
- Skipped execution should keep the SQL draft and explanation visible so the user can inspect why it was not run.

### 4. Validation & Error Matrix

- HTTP/network failure -> frontend transport error state.
- HTTP 200 + `status="error"` -> backend business/execution error state.
- HTTP 200 + `debug.execution_gate.allowed=false` -> SQL draft / not executed state.
- HTTP 200 + empty rows and no skipped gate -> normal empty-result state.

### 5. Good/Base/Bad Cases

- Good: low-confidence SQL shows “SQL 草案，未自动查询数据库” and does not imply empty database results.
- Base: valid executed SQL with no rows shows a normal empty-result message.
- Bad: every HTTP 200 response is marked as successful regardless of `status` or `execution_gate`.

### 6. Tests Required

- `pnpm build` must pass after response handling changes.
- Manual/UI check: loading, backend error, skipped execution, empty result, tabular result, and debug sections.

### 7. Wrong vs Correct

#### Wrong

```ts
if (data.rows.length === 0) {
  resultState.value = 'empty'
}
```

#### Correct

```ts
if (data.status === 'error') {
  resultState.value = 'error'
} else if (data.debug?.execution_gate?.allowed === false) {
  resultState.value = 'draft'
} else if (data.rows.length === 0) {
  resultState.value = 'empty'
}
```
