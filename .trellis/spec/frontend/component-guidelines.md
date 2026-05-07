# Component Guidelines

> Vue component patterns for this project.

---

## Composition API

- Use **`<script setup lang="ts">`** for new and existing root UI (`App.vue`).
- Prefer `ref` / `computed` for local UI state; avoid Options API unless maintaining legacy code.

---

## Structure

- **Template**: keep loading/error/success states explicit (the app already distinguishes `idle` | `loading` | `success` | `error`).
- **Presentation**: tables for rows should tolerate empty `columns` by deriving headers from first row (`derivedColumns` pattern in `App.vue`).
- **Accessibility**: preserve semantic headings and buttons when splitting components; associate labels with inputs.

---

## Styling

- Global styles live in `src/style.css`; component-specific styles use `<style scoped>` when extracting components.

---

## Forbidden Patterns

- Duplicating fetch logic across multiple components without a composable (when the app grows past one screen).
- Hardcoding backend origin `http://127.0.0.1:8787` in browser-facing fetch URLs in dev — use `/api` with Vite proxy.

---

## Common Mistakes

- Drifting frontend `QueryResponse` fields from backend `NLQueryResponse` (e.g. `explanation` vs legacy `notes`); keep parsers defensive as in `App.vue`.
