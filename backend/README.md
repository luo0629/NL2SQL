# Backend

SQLAgent backend is the application control layer for the repository's NL2SQL flow.
It owns schema loading, agent orchestration, SQL validation, controlled execution, and response shaping.

## What the backend currently does

- Serves `GET /api/health` and `POST /api/query`
- Loads real schema metadata into the agent workflow
- Selects relevant tables and renders relation-aware schema context
- Generates SQL through the LangGraph pipeline
- Validates read-only SQL before execution
- Returns structured execution results, summaries, and debug metadata

## Execution model

The main path is:

```text
load_schema_catalog
-> intent_parser
-> schema_retriever
-> sql_generator
-> sql_validator
-> value_validator
-> sql_executor
-> result_formatter
```

The backend is designed so generation, validation, and execution stay as separate boundaries.
That separation is important for safety, debugging, and future production controls.

## Provider and local development behavior

- Default mode can still run through `mock`/fallback behavior for stable local development.
- If you set `LLM_PROVIDER=xiaomi` and provide `XIAOMI_API_KEY`, the backend can call Xiaomi through an OpenAI-compatible endpoint.
- If you set `LLM_PROVIDER=zhipu` and provide `ZHIPU_API_KEY`, the backend can call GLM through an OpenAI-compatible endpoint.
- SQL always goes through the validator and executor path rather than being returned straight from the router.

## Join reliability direction

Stage 1 join reliability work is already reflected in backend schema and prompt context:

- relation-aware schema rendering
- join hints and confidence signals
- cross-table diff guidance for repeated field names
- safer preference for business keys over generic audit/common fields

Later stages will continue with stronger ranking, validation, and data-quality-driven relation governance.

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

Project docs:

```text
../README.md
../docs/NL2SQL_AGENT_IMPLEMENTATION_TODO.md
../docs/BEGINNER_SQLAGENT_ROADMAP.md
```
