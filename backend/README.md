# Backend

## Structure

The backend now follows a layered `app/` layout for configuration, routers, schemas, services, agent orchestration, database utilities, validators, RAG helpers, prompts, and tests.

## Current learning-friendly SQLAgent mode

- Default mode is `mock`, so the backend can run without a real model key.
- If you set `LLM_PROVIDER=zhipu` and provide `ZHIPU_API_KEY`, the project will try to call GLM through the OpenAI-compatible endpoint.
- All generated SQL goes through a read-only validator before it is returned.
- Real database execution is still intentionally kept out of the first learning phase.

## Development

Start the FastAPI development server:

```powershell
uv run dev.py
```

Run tests:

```powershell
uv run pytest
```

Read the beginner roadmap:

```text
../docs/BEGINNER_SQLAGENT_ROADMAP.md
```

Health check:

```text
http://127.0.0.1:8787/api/health
```
