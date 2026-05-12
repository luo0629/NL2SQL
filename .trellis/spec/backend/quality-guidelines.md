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
- schema catalog shape in `app/rag/schema_models.py`
- business semantic derivation or overrides in `app/rag/business_semantics.py`

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
- Assuming schema enrichment keys always use short table names; generated config may emit qualified names, so enrichment lookup must handle both forms
- Assuming runtime join probes are only needed when a table pair has multiple sibling candidates; a single dirty shared-key candidate can also need bounded validation
- Assuming offline governance artifacts can be treated as harmless side files; they are contract-sensitive outputs and must not leak secrets, fake perfect coverage, or silently diverge from schema sync state
- Assuming join ranking, validation, or governance signals are useful just because they appear in schema_context; if the validator path does not enforce them, weaker joins can still reach execution unchanged
- Assuming a table-level disabled key is handled just by hiding it from the prompt; if `nodes.py` selection logic and validator enforcement do not both honor the config, the key can still leak into generated SQL
- Assuming stable `ORDER BY` automatically means semantically correct ordering; fallback list queries can still look wrong if they sort by technical primary keys instead of business time/identifier fields
- Assuming old sample database names are harmless if they appear only in prompt examples or `.env.example`; these still shape model outputs and new-environment bootstrap behavior, so they count as runtime/initialization residue
- Assuming `deleted` being marked as `internal` is enough to keep deleted rows out of normal query results; without explicit generation-layer filtering, soft-deleted rows can still leak into default query results
- Assuming every `is_enable` column in the database should automatically gain the same enum semantics; in this project the current MVP is intentionally limited to the active `.env` table scope plus an explicit allowlist
