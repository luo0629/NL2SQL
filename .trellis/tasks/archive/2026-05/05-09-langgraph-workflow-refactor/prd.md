# brainstorm: langgraph workflow refactor

## Goal

Refactor the SQLAgent backend LangGraph workflow so the graph owns more of the NL2SQL control flow, state contracts become clearer, and retry/routing behavior becomes easier to test and evolve toward a production-grade SQL agent.

## What I already know

* The current backend path is FastAPI -> AgentService -> `run_agent()` -> LangGraph -> `NLQueryResponse`.
* `run_agent()` currently loads schema catalog before graph invocation and returns early on schema timeout/failure.
* `AgentState` currently contains duplicate/overlapping fields such as `question` / `user_input`, `generated_sql` / `sql`, and `query_result` / `rows`.
* Validation retry state is partially reset by `_build_sql_generator_result()`, so retry context is not modeled as accumulated graph state.
* `sql_validator` and `value_validator` both route through `_after_sql_validation()`, which works but mixes semantics.
* `get_agent_graph()` caches the compiled graph using object identity including `id(catalog)`.
* Existing tests already assert graph execution, retry behavior, timeout handling, and API contract stability.
* Backend specs require structured failures through graph state / `NLQueryResponse`, not raw exceptions.

## Assumptions (temporary)

* MVP should improve architecture and testability without breaking the existing HTTP response contract.
* Frontend changes are not required if `NLQueryResponse` remains stable.
* We should prefer staged refactor over a full redesign introducing persistence or HITL immediately.

## Open Questions

* None.

## Requirements (evolving)

* Move schema catalog loading into graph-owned flow or an equivalent graph-visible node/state transition.
* Define a clearer canonical state contract for request input, generated SQL, validation status, and execution result.
* Preserve existing `NLQueryResponse` fields and status semantics unless explicitly expanded later.
* Keep validation retry behavior bounded and testable.
* Split SQL validation routing and value validation routing into explicit graph semantics.
* Maintain sanitized error behavior for schema load, validation failure, timeout, and execution failure.
* Keep current backend command/test workflow and reuse existing test patterns.
* Follow Approach A scope: fix graph ownership and state/routing contracts now, while deferring persistence/HITL expansion.

## Acceptance Criteria (evolving)

* [ ] Graph flow owns schema-load success/failure handling instead of `run_agent()` returning before graph execution.
* [ ] `AgentState` canonical fields are documented and duplicate fields are removed or confined to boundary mapping.
* [ ] Retry routing remains bounded and covered by focused unit tests.
* [ ] Existing API contract for `NLQueryResponse` still passes integration/unit expectations.
* [ ] Error summaries remain sanitized and structured.

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* Frontend UX redesign
* Full multi-agent redesign
* New model provider features
* Large executor/database capability expansion unrelated to graph ownership
* Compile cache / graph factory cleanup beyond what is strictly required to preserve current behavior
* Checkpointer / HITL / RetryPolicy platform expansion

## Technical Approach

Selected direction: Approach A (Minimal graph ownership refactor).

Current feasible approaches:

### Approach A: Minimal graph ownership refactor (Recommended)

* Add a schema-load node at graph entry.
* Normalize `AgentState` to a smaller canonical core while preserving response mapping in service/formatter layers.
* Split routing helpers so SQL validation and value validation have explicit semantics.
* Keep compile/invoke model simple; defer persistence/HITL.

Pros:
* Lowest risk to existing tests and API contract
* Fixes the largest architectural issues first
* Good base for later persistence/HITL

Cons:
* Does not fully adopt advanced LangGraph features yet

### Approach B: Medium refactor with graph factory cleanup

* Everything in Approach A
* Also decouple compiled graph structure from request-scoped objects like catalog
* Rework graph construction/caching around stable dependencies and state/config inputs

Pros:
* Better LangGraph architecture and cleaner dependency boundary
* Avoids weak compile cache semantics

Cons:
* More files touched, higher regression risk
* May require broader test updates

### Approach C: Full LangGraph platform step

* Everything in Approach B
* Introduce checkpointer hooks, RetryPolicy review, and explicit human/persistence extension points now

Pros:
* Strongest long-term architecture
* Maximizes LangGraph-native patterns early

Cons:
* Highest scope and slowest path to merge
* Likely overkill for current repo maturity

## Decision (ADR-lite)

**Context**: The current LangGraph workflow has the largest pain around graph ownership boundaries, duplicate state fields, and retry/routing clarity, while the HTTP response contract is already covered by tests and should remain stable.
**Decision**: Choose strict Approach A for MVP. Refactor graph entry and state/routing contracts first; defer compile-cache cleanup, persistence/HITL, and larger platformization work.
**Consequences**: This reduces regression risk and keeps the change set reviewable, but cache/factory cleanup and deeper LangGraph-native infrastructure will remain explicit follow-up work.

## Technical Notes

* Relevant files:
  * `backend/app/agent/graph.py`
  * `backend/app/agent/state.py`
  * `backend/app/agent/nodes.py`
  * `backend/app/services/agent_service.py`
  * `backend/app/schemas/query.py`
  * `backend/tests/unit/test_agent_graph_schema_plan.py`
  * `backend/tests/unit/test_value_validation.py`
  * `backend/tests/unit/test_query_service.py`
* Repo constraints from spec:
  * Keep routers thin and return structured backend failures.
  * Treat graph routing and `AgentState` as contract-sensitive.
  * Preserve `status in {mock, ready, error}` unless backend+frontend change together.
* Existing graph tests still describe the current expected six-node pipeline and bounded retry behavior.
