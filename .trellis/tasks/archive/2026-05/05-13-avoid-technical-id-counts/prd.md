# brainstorm: avoid technical id counts

## Goal

Fix NL2SQL plain COUNT behavior so generated SQL does not rely on technical `id` / `*_id` fields when the user asks business-oriented count questions. The agent should prefer a question-relevant business field when one is available, otherwise use neutral `COUNT(*)` instead of pretending technical IDs are the business counting unit.

## What I already know

* User reports generated SQL still sometimes depends on `id` for COUNT.
* Backend spec already requires plain COUNT questions to avoid blindly defaulting to `COUNT(id)`.
* Existing prompt includes a COUNT rule in `backend/app/agent/nodes.py`.
* Existing schema context can render `Preferred COUNT expression` through `_preferred_count_strategy()`.
* Existing validator can reject technical count targets through `_count_selection_validation_message()`.
* Current validator only applies this COUNT repair when exactly one `relevant_tables` entry exists, so multi-table count SQL can still leak `COUNT(id)`.
* Current technical target detection treats `COUNT(1)` as technical too, even though spec says neutral fallback should be `COUNT(*)`.

## Assumptions (temporary)

* MVP scope is plain business count questions such as “多少条/数量/个数/统计”.
* Explicit ID/code/number questions should still be allowed to count identifier fields.
* Full aggregate governance such as `COUNT(DISTINCT ...)`, SUM/AVG correctness, and deduplication semantics is out of scope unless explicitly requested.

## Open Questions

* None for MVP.

## Requirements (evolving)

* Plain business count questions must not use technical primary keys or foreign keys as the default count expression.
* The agent should analyze question semantics and relevant schema to choose the most question-relevant business count field when available.
* If one relevant table has a strong business count field, generated SQL should prefer that field, e.g. `COUNT(`order_no`)`.
* In multi-table SQL, validator repair should try to map a technical count target back to its table and recommend that table's preferred business count expression.
* If the target table cannot be determined or no strong business field is known, generated SQL should use `COUNT(*)`.
* Validator retry messages should steer the model away from technical count targets before execution.
* Explicit ID/count-by-ID requests must remain supported.

## Acceptance Criteria (evolving)

* [ ] Prompt guidance clearly says plain count fallback is business field -> `COUNT(*)`, not `COUNT(id)`.
* [ ] Single-table plain count `COUNT(id)` is rejected and repaired toward the preferred business expression or `COUNT(*)`.
* [ ] Multi-table plain count `COUNT(table.id)` is rejected and repaired toward that table's business count expression when possible.
* [ ] Multi-table plain count `COUNT(1)` is rejected and repaired toward a question-relevant business expression when possible, otherwise `COUNT(*)`.
* [ ] Explicit identifier count questions do not get blocked by the plain-count repair.
* [ ] Focused backend unit tests pass.

## Decision (ADR-lite)

**Context**: The user wants COUNT to be derived from the business meaning of the question instead of defaulting to table IDs.
**Decision**: Prefer `COUNT(business_field)` by analyzing the question and table schema; use `COUNT(*)` only when no reliable business field can be determined.
**Consequences**: This keeps plain count behavior closer to business semantics while preserving a neutral fallback for ambiguous cases. Full DISTINCT/SUM/AVG governance remains out of scope.

## Definition of Done (team quality bar)

* Tests added/updated for unit-level COUNT behavior.
* Relevant backend tests pass with `uv --project "backend" run pytest ...`.
* No API contract or frontend response shape changes.
* No git commit until user tests and explicitly approves.

## Out of Scope (explicit)

* Full aggregate-governance framework for DISTINCT/SUM/AVG.
* Query execution result shape changes.
* Frontend changes.
* Schema sync or governance artifact redesign.

## Technical Notes

* Relevant code: `backend/app/agent/nodes.py` (`_build_sql_generation_prompt`, `_preferred_count_strategy`, `_extract_plain_count_targets`, `_is_technical_count_target`, `_count_selection_validation_message`, `sql_validator`).
* Relevant tests: `backend/tests/unit/test_agent_graph_schema_plan.py`.
* Relevant spec: `.trellis/spec/backend/database-guidelines.md` lines around plain COUNT selection and validation.
* Existing limitation likely causing the report: `_count_selection_validation_message()` returns early when `len(relevant_tables) != 1`, which protects only the single-table path.
