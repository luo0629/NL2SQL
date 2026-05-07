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
