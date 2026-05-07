# Directory Structure

> How frontend code is organized in this project.

---

## Overview

Frontend root is `frontend/`. The current UI is intentionally small: one Vue 3 root component, one entry file, one global stylesheet, and Vite dev-server proxying to the backend. There is no feature-folder split yet.

---

## Directory Layout

```text
frontend/
├── index.html
├── package.json
├── vite.config.ts            # port 4242, strictPort, /api proxy -> 127.0.0.1:8787
├── src/
│   ├── main.ts               # createApp(App).mount('#app')
│   ├── App.vue               # full NL2SQL workspace UI and fetch flow
│   ├── style.css             # global page styling
│   └── assets/
└── tsconfig*.json
```

---

## Module Organization

Current reality:

- `src/App.vue` owns query input, request lifecycle, status messaging, response parsing, table rendering, and debug display.
- `src/main.ts` stays minimal and only mounts the app.
- `src/style.css` carries the shared visual system for the page.

This repo does **not** yet have `components/`, `composables/`, or `types/` directories. Document and preserve that fact unless the codebase is actually refactored.

---

## API Boundary Convention

Browser code should call relative `/api/...` paths and rely on the Vite proxy in `frontend/vite.config.ts` during local development.

Real example:

- `App.vue` posts to `/api/query`
- `vite.config.ts` proxies `/api` to `http://127.0.0.1:8787`

---

## Naming Conventions

- Root SFC remains `App.vue`
- If components are extracted later, use `PascalCase.vue`
- If composables are introduced later, use `useSomething.ts`

---

## Forbidden Patterns

- Pretending shared folders already exist when adding spec guidance
- Hardcoding backend origins inside browser fetch calls when `/api` proxy is the local contract

---

## Common Mistakes

- Writing guidance as if this were already a multi-page app with routed screens and shared stores
- Updating backend response shape without checking the parsing logic concentrated in `frontend/src/App.vue`
