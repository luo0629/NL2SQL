# Backend

## Structure

The backend now follows a layered `app/` layout for configuration, routers, schemas, services, agent orchestration, database utilities, validators, RAG helpers, prompts, and tests.

## Development

Start the FastAPI development server:

```powershell
uv run dev.py
```

Run tests:

```powershell
uv run pytest
```

Health check:

```text
http://127.0.0.1:8787/api/health
```
