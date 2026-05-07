# Quality Guidelines

> Code standards, testing, and validation conventions for the backend.

---

## Stack

- Python `>=3.12`
- FastAPI
- Pydantic settings
- LangGraph
- SQLAlchemy async
- pytest for tests
- `uv` for dependency management and command execution

Use the versions and commands defined in `backend/pyproject.toml` and project `CLAUDE.md` as the source of truth.

---

## Command Conventions

Run backend work from `backend/`:

```text
uv sync
uv run dev.py
uv run pytest
uv run pytest tests/unit/test_query_service.py
uv run pytest tests/integration/test_query_router.py
```

Do not switch package managers or ad-hoc runner scripts for routine backend work.

---

## Testing Strategy

| Scope | Location | Real focus |
|-------|----------|------------|
| Unit | `backend/tests/unit/` | validator rules, graph behavior, RAG helpers, executor behavior |
| Integration | `backend/tests/integration/` | FastAPI router contract and request/response behavior |

`backend/tests/conftest.py` is the reference test-wiring pattern:

- override `get_agent_service`
- stub `LLMService.build_chat_model()` to return `None`
- stub `SQLExecutor.execute()` to return deterministic rows

This keeps endpoint tests stable without requiring a live model backend.

---

## Contract-Sensitive Changes

Always add or rerun focused tests when changing any of these:

- `app/schemas/query.py`
- `app/agent/state.py`
- graph routing in `app/agent/graph.py`
- validator behavior in `app/validator/sql_validator.py`
- executor result shape in `app/database/executor.py`

These modules define contracts shared across multiple layers.

---

## Design Principles

1. Keep routers thin and orchestration in services/agent layers.
2. Centralize SQL safety in `SQLValidator` and the executor path.
3. Prefer deterministic, testable node behavior over hidden side effects.
4. Align backend response changes with frontend parsing immediately.

---

## Forbidden Patterns

- Moving business orchestration into route handlers
- Duplicating SQL safety checks across unrelated modules instead of reusing `SQLValidator`
- Changing shared agent-state keys without checking all downstream readers

---

## Common Mistakes

- Updating `NLQueryResponse` without updating `frontend/src/App.vue`
- Forgetting that graph caching and test doubles can affect assertions around the compiled agent flow
