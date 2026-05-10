# brainstorm: langgraph phase-b cleanup

## Goal

Clean up the SQLAgent backend LangGraph factory/caching semantics after Phase A so the compiled graph is no longer coupled to request-scoped identity, and decide whether backward-compatibility shims for old state keys should remain or be removed, while keeping the current HTTP contract stable.

## What I already know

* Phase A is already complete: schema load runs inside the graph, AgentState was narrowed, validation/value routing was split, and backend tests were green.
* `backend/app/agent/graph.py` still keeps a module-global `_compiled_graph` and `_graph_executor_key`.
* `get_agent_graph()` currently builds its cache key from object identity, including `id(rag_service)`, `id(llm_service)`, `id(validator)`, `id(executor)`, and previously even request-scoped catalog identity.
* `run_agent()` now calls `get_agent_graph(rag_service, llm_service, validator, executor)` without catalog input, so the worst request-scoped cache coupling is gone, but the graph factory is still object-identity-based and opaque.
* `backend/app/agent/nodes.py` still has compatibility shims in `_question_text()` and `_current_sql()` that read legacy `question` and `sql` keys.
* Existing unit tests still call `reset_agent_graph()` directly and some direct node tests may rely on those compatibility shims.
* Current repo constraints: keep `NLQueryResponse` stable, keep routers thin, and avoid broad platformization like checkpointer/HITL in this phase.

## Assumptions (temporary)

* Phase B should stay backend-only.
* Phase B should preserve current endpoint behavior and response schema.
* We should improve graph factory clarity without turning this into a deeper LangGraph platform rewrite.

## Open Questions

* None.

## Requirements (evolving)

* Simplify graph factory / compile cache semantics in `backend/app/agent/graph.py`.
* Avoid request-scoped or unnecessary identity coupling in graph caching decisions.
* Preserve current HTTP/API contract and current graph behavior.
* Keep tests stable or update them intentionally where behavior/contracts become clearer.
* Keep legacy state-key compatibility shims for `question` / `sql` for this phase.
* Follow Approach A scope: cache/factory cleanup now, deeper compatibility cleanup later.

## Acceptance Criteria (evolving)

* [ ] Graph compile/cache behavior is simpler and easier to reason about than the current object-identity tuple approach.
* [ ] `run_agent()` and tests still pass without changing `NLQueryResponse` shape.
* [ ] Compatibility shim decision is explicit: either removed with updated tests/callers, or retained with documented rationale.
* [ ] Full backend tests remain green.

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* Checkpointer / HITL / RetryPolicy platform expansion
* New model-provider capabilities
* Frontend changes
* Another major AgentState redesign beyond Phase A cleanup
* Compatibility-shim removal for legacy `question` / `sql`
* `reset_agent_graph()` / graph-test helper semantic cleanup beyond what is strictly necessary for current tests

## Technical Approach

Selected direction: Approach A (Cache cleanup only).

### Approach A: Cache cleanup only (Recommended)

* Keep a single compiled graph, but simplify `get_agent_graph()` so cache invalidation depends only on stable graph-construction dependencies we actually care about.
* Keep compatibility shims for `question` / `sql` for one more phase.

Pros:
* Lowest risk
* Improves graph-factory clarity immediately
* Avoids touching direct node-call tests/callers unless necessary

Cons:
* Leaves temporary backward-compatibility code in place

### Approach B: Cache cleanup + shim removal

* Simplify graph factory/cache semantics.
* Remove legacy `question` / `sql` fallback from `nodes.py` helpers and update tests/callers to canonical state only.

Pros:
* Cleaner Phase B end-state
* Removes lingering ambiguity from state access

Cons:
* Slightly broader regression surface
* Forces direct node-call tests/helpers to be updated now

### Approach C: No cache, explicit factory only

* Remove module-global compiled graph cache entirely.
* Compile graph per invocation or per explicit builder call.
* Remove or keep shims as a secondary choice.

Pros:
* Simplest semantics to explain
* No hidden mutable global cache state

Cons:
* May give up harmless compile reuse
* More architectural churn than needed for this repo today

## Decision (ADR-lite)

**Context**: Phase A already moved schema loading into the graph and stabilized the state contract, but the graph factory still uses opaque module-global cache semantics tied to object identity. The remaining `question` / `sql` shims are compatibility debt, but not the highest-risk issue.
**Decision**: Choose strict Approach A for Phase B. Clean up graph factory / compile cache semantics first, while keeping compatibility shims and existing `reset_agent_graph()` test helper semantics for one more phase.
**Consequences**: Graph construction becomes easier to reason about with lower regression risk, but compatibility cleanup, stricter canonical-state enforcement, and test-helper cleanup remain explicit follow-up work.

## Technical Notes

* Relevant files:
  * `backend/app/agent/graph.py`
  * `backend/app/agent/nodes.py`
  * `backend/tests/unit/test_agent_graph_schema_plan.py`
* Constraints:
  * Keep `status in {mock, ready, error}` stable.
  * Preserve current API contract mapping through `AgentService`.
  * Do not expand scope into persistence/HITL.
