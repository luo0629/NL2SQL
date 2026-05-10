# brainstorm: SQL prompt field matching rules

## Goal

Update the SQL generation prompt so model-generated filters choose safer matching operators: enum fields use exact matching against schema enum mappings, name-like string fields use LIKE fuzzy matching, and uncertain field types prefer LIKE over equality.

## What I already know

* User wants to modify the SQL generation node prompt.
* Target prompt function is `backend/app/agent/nodes.py::_build_sql_generation_prompt`.
* The current prompt already constrains read-only SQL, schema-only tables/fields, preferred output columns, stable ordering for LIMIT, and default `LIMIT 200`.
* Schema context already includes enum mapping text such as `enum_mapping: 未上架=0, 起售=1` in existing tests.

## Assumptions (temporary)

* This task should only change prompt behavior, not deterministic fallback SQL generation or validator logic.
* Acceptance can be covered by a unit test that inspects the generated prompt contents.

## Open Questions

* None blocking.

## Requirements

* Add SQL generation prompt rules for field matching.
* Enum-value fields must use exact matching (`=` / `IN`) and values must come from the Schema enum mapping.
* Name-like string fields such as city, person name, customer name, product name, dish name, and title should default to `LIKE` fuzzy matching.
* If field type/category is uncertain, prefer `LIKE` over equality.
* Preserve existing hard safety and schema-boundary prompt rules.

## Acceptance Criteria

* [ ] `_build_sql_generation_prompt` includes enum exact-match guidance tied to schema enum mappings.
* [ ] `_build_sql_generation_prompt` includes name-like string field `LIKE` guidance.
* [ ] `_build_sql_generation_prompt` includes uncertain-field `LIKE` fallback guidance.
* [ ] Relevant unit tests pass.

## Definition of Done

* Tests added/updated where appropriate.
* Lint/typecheck not required unless touched code indicates it.
* No API contract changes.
* No frontend changes.

## Out of Scope

* Changing SQL validator behavior.
* Changing fallback SQL generation templates.
* Adding schema metadata extraction beyond existing enum mapping context.
* Executing real database queries.

## Technical Notes

* Inspected `backend/app/agent/nodes.py::_build_sql_generation_prompt`.
* Likely test area: `backend/tests/unit/test_agent_graph_schema_plan.py` or another prompt-focused unit test.
