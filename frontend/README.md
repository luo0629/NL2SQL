# Frontend

This frontend is the query workspace for SQLAgent.
It is not a generic Vue template page anymore; it is the UI surface for sending natural-language questions and reviewing SQLAgent outputs.

## What it shows

- natural-language input
- generated SQL
- query parameters
- execution summary
- returned rows and columns
- debug metadata when available

## Local development

```powershell
pnpm install
pnpm dev
```

Default URL:

```text
http://127.0.0.1:4242
```

The frontend proxies `/api` requests to the backend at `http://127.0.0.1:8787` through `vite.config.ts`.

## Related docs

- `../README.md`
- `../docs/NL2SQL_AGENT_IMPLEMENTATION_TODO.md`
- `../docs/BEGINNER_SQLAGENT_ROADMAP.md`
