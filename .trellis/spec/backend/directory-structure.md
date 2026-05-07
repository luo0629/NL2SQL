# Directory Structure

> How backend code is organized in this project.

---

## Overview

Backend root is `backend/`. Application code lives under `backend/app/` with a layered layout: configuration, HTTP routers, orchestration services, LangGraph agent flow, schema retrieval helpers, database execution, validators, and Pydantic schemas. Tests live in `backend/tests/` and are split into `unit/` and `integration/`.

---

## Directory Layout

```text
backend/
├── app/
│   ├── main.py                # FastAPI factory + lifespan wiring
│   ├── config.py              # Pydantic Settings from .env
│   ├── dependencies.py        # FastAPI Depends() wiring
│   ├── agent/                 # AgentState, LangGraph build, node functions
│   ├── routers/               # HTTP routes (`query.py`)
│   ├── services/              # AgentService, RagService, LLMService
│   ├── rag/                   # Schema sync, retriever, linkers, planners, repairers
│   ├── database/              # engine, session, SQLExecutor
│   ├── validator/             # SQLValidator read-only policy
│   ├── schemas/               # Request/response and execution models
│   ├── prompts/               # Prompt templates and few-shot assets
│   ├── core/                  # logging, middleware, cache helpers
│   ├── models/                # ORM base scaffolding
│   └── utils/                 # exceptions and helpers
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/
├── dev.py                     # Local dev launcher
└── pyproject.toml
```

---

## Module Organization

| Layer | Responsibility | Real examples |
|-------|----------------|---------------|
| `routers/` | HTTP boundary and response model | `app/routers/query.py` keeps `POST /api/query` as one-line delegation to `AgentService.generate_sql()` |
| `services/` | Dependency assembly and high-level orchestration | `app/services/agent_service.py` injects RAG, LLM, validator, and executor into `run_agent()` |
| `agent/` | Shared agent state and LangGraph sequencing | `app/agent/graph.py` builds the full pipeline and `app/agent/state.py` defines the shared contract |
| `rag/` | Schema retrieval and reasoning helpers | `app/rag/schema_sync.py`, `app/rag/schema_linker.py`, `app/rag/value_linker.py`, `app/rag/join_path_planner.py` |
| `database/` | SQL execution and connection lifecycle | `app/database/executor.py` validates and executes SQL through SQLAlchemy async connections |
| `validator/` | SQL safety enforcement | `app/validator/sql_validator.py` owns read-only validation and plan provenance checks |

---

## Query Path Convention

For any change in the main NL2SQL flow, trace the whole chain:

1. `app/routers/query.py` receives the request
2. `app/services/agent_service.py` calls `run_agent()`
3. `app/agent/graph.py` runs the graph
4. `app/agent/nodes.py` performs step-level work
5. `app/database/executor.py` executes validated SQL
6. `app/schemas/query.py` defines the API contract returned to the frontend

The current graph is broader than the earlier teaching skeleton. The real node order is:

`query_understanding` → `retrieve_schema` → `schema_linking` → `value_linking` → `join_path_planning` → `build_semantic_brief` → `sql_planning` → `generate_sql` → `validate_sql` → (`sql_repairing` retry loop or `execute_sql`) → `finalize_response`

---

## Naming Conventions

- Python modules: `snake_case.py`
- Classes: `PascalCase`
- Public state keys and schema fields: `snake_case`
- Test files: `test_*.py` under `tests/unit/` or `tests/integration/`

---

## Forbidden Patterns

- Moving orchestration logic from `services/` or `agent/` into route handlers
- Executing user-generated SQL from `rag/` helpers or routers
- Adding new agent state keys in one node without checking every downstream consumer in `app/agent/state.py`

---

## Common Mistakes

- Updating only the old `retrieve_schema -> generate_sql -> validate_sql` mental model and forgetting the newer planning/linking nodes
- Changing `NLQueryResponse` fields without updating frontend parsing in `frontend/src/App.vue`
