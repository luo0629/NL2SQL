# brainstorm: value existence validation before SQL execution

## Goal

Add a pre-execution value existence validation step to the NL2SQL agent so generated SQL can be corrected before execution when WHERE string equality predicates use values that do not exist in the real database. The validator should parse SQL, extract relevant predicates, verify values, suggest similar real values, write a corrective error prompt into agent state, and trigger the existing retry mechanism.

## What I already know

* The user wants this in an NL2SQL Agent before SQL execution.
* Generated SQL should be parsed with `sqlglot`.
* The check targets WHERE conditions containing string-type equality matches.
* For each extracted value, the system should query the database to verify existence.
* Missing values should produce fuzzy-query suggestions using similar real database values.
* The question and suggestions should be written back into state as an error prompt.
* The error prompt should trigger retry so the LLM can fix SQL.
* Project direction prioritizes real SQL execution, stronger schema reasoning, safety boundaries, and regression coverage.

## What I discovered from repo/context

* Current graph path is `intent_parser -> schema_retriever -> sql_generator -> sql_validator -> sql_executor -> result_formatter` in `backend/app/agent/graph.py`.
* Retry routing is centralized in `_after_sql_validation`: if `validation_error` is set and `retry_count < max_retries`, route back to `sql_generator`.
* `backend/app/agent/nodes.py::sql_validator` already validates read-only SQL and optionally runs MySQL EXPLAIN before execution.
* `backend/app/agent/nodes.py::sql_executor` is the only generated SQL execution node.
* `AgentState` already has `validation_error`, `validation_errors`, `validation_issues`, `retry_count`, and `debug_trace`; new value-check failures can reuse this contract.
* `SQLExecutor` has `execute()` and `explain()` but no generic scalar/probe method yet.
* `SchemaCatalog` exposes tables/columns with `data_type`, `semantic_role`, `description`, and business semantics; it can identify string columns and avoid numeric/date/internal fields.
* `sqlglot` is not currently listed in `backend/pyproject.toml`; adding this feature requires adding a backend dependency.
* Context7 docs confirm `sqlglot.parse_one(sql, dialect="mysql")`, AST traversal, `exp.EQ`, `exp.Column`, and `exp.Literal` are the right primitives.

## Assumptions (temporary)

* This should reuse the existing agent retry loop rather than adding a parallel correction pipeline.
* This should run after SQL safety validation and before SQL execution, because database probing must not happen for unsafe SQL.
* This should use parameterized SQL for probe queries and avoid executing generated SQL while validating values.
* MVP should focus on simple mappable predicates; complex subqueries/functions can be out of scope unless confirmed.
* Fuzzy suggestions should be bounded, for example top 5 `LIKE '%value%'` matches per missing value.

## Open Questions

* None blocking.

## Requirements

* Add `sqlglot` as a backend dependency.
* Parse generated SQL with MySQL dialect via `sqlglot`.
* Add a dedicated pre-execution `value_validator` graph node after SQL safety validation and before SQL execution.
* Extract mappable WHERE predicates for string values:
  * `column = 'value'`
  * `table.column = 'value'`
  * simple alias references such as `d.name = 'value'`
  * `column IN ('value1', 'value2')` / qualified or simple-aliased variants
* Verify extracted values against the real database before executing generated SQL.
* For values that do not exist, query similar values with fuzzy matching.
* Write a corrective error prompt into state, including the original question, table/column, missing value, and suggested real values.
* Trigger the existing retry mechanism so the LLM can repair SQL.
* Preserve SQL safety boundaries and avoid executing unsafe generated SQL.
* Keep database probe queries parameterized, read-only, bounded, and timeout-aware.
* Skip unmappable complex predicates safely rather than failing the query solely because extraction is unsupported.

## Acceptance Criteria

* [ ] SQL with existing string equality values proceeds to execution.
* [ ] SQL with existing string `IN (...)` values proceeds to execution.
* [ ] SQL using simple table aliases can map alias-qualified string predicates back to schema columns.
* [ ] SQL with a missing string equality value does not execute the generated SQL and triggers retry.
* [ ] SQL with a missing string `IN (...)` value does not execute the generated SQL and triggers retry.
* [ ] Retry prompt includes original user question, table/column, missing value, and suggested real values.
* [ ] Numeric/date/enumerated/internal-ID equality predicates are not incorrectly treated as string value checks.
* [ ] Unsupported complex predicates such as functions, subqueries, and hard-to-map OR cases are skipped safely in MVP.
* [ ] Probe queries are parameterized and bounded.
* [ ] Unit tests cover extraction, alias mapping, value probing, and retry state behavior.
* [ ] Related backend graph tests pass or external DB-dependent failures are documented.

## Definition of Done (team quality bar)

* Tests added/updated for extraction, validation, and graph retry behavior.
* Relevant backend tests pass or failures are documented with cause.
* No frontend/API contract changes unless explicitly required.
* Rollout/rollback considered if database probing fails or is disabled.

## Expansion Sweep

### Future evolution

* Could later support `IN (...)`, OR predicates, aliases across joins, and configurable similarity strategies.
* Could later cache distinct values for low-cardinality dimensions to reduce probe query cost.

### Related scenarios

* Should stay consistent with SQL validator retry behavior and result formatter error reporting.
* Should respect existing schema catalog and business semantic enum handling rather than creating a separate metadata path.

### Failure & edge cases

* Parser failure, unmappable columns, ambiguous unqualified columns, DB probe timeout, and no suggestions should fail safely.
* Probe queries must not run for unsafe generated SQL and must not execute the generated SQL itself.

## Feasible Approaches

### Approach A: Dedicated `value_validator` graph node after `sql_validator` (Recommended)

* How it works: `sql_validator` handles safety/EXPLAIN; new `value_validator` parses and probes values; its failures set `validation_error` and increment `retry_count`; routing function after value validation reuses retry-to-generator behavior.
* Pros: clean separation, observable debug trace, easy tests, avoids bloating SQL safety validator.
* Cons: requires graph topology change and a small new node/service/helper.

### Approach B: Extend `sql_validator` to include value existence checks

* How it works: after read-only/EXPLAIN passes, `sql_validator` also parses/probes values before returning success.
* Pros: minimal graph change and automatically reuses current `_after_sql_validation`.
* Cons: mixes structural safety validation with data existence probing; harder to disable/tune independently.

### Approach C: Put value check inside `sql_executor` before execution

* How it works: executor probes values and returns an execution-like error if missing.
* Pros: close to database access and avoids graph changes.
* Cons: cannot naturally trigger LLM retry because retry routing happens before executor; least aligned with requested behavior.

## Decision (ADR-lite)

**Context**: Value existence validation needs database probes and LLM retry behavior, but SQL safety validation and generated SQL execution should remain separate responsibilities.

**Decision**: Implement the recommended dedicated `value_validator` graph node after `sql_validator` and before `sql_executor`. MVP scope is Approach 2: support string equality predicates, `IN (...)`, and simple table aliases; skip complex OR/function/subquery cases safely.

**Consequences**: This adds a graph node and a small database probe capability, but preserves clean separation and reuses `validation_error`/`retry_count` for repair. More complex predicate coverage can be added later without changing the retry contract.

## Technical Approach

* Add sqlglot parsing helpers for table alias discovery and predicate extraction.
* Add a bounded, parameterized value-probe method near the database execution layer.
* Add `value_validator` node that:
  * runs only after `sql_validator` succeeds;
  * maps extracted predicates to schema string columns;
  * checks exact existence;
  * fetches fuzzy suggestions for missing values;
  * sets `validation_error`, `validation_errors`, `validation_issues`, `retry_count`, and `debug_trace` when repair is needed.
* Add graph routing so value-validation failures retry `sql_generator`; success continues to `sql_executor`.
* Keep unsupported predicates as skipped debug info, not hard failures.

## Implementation Plan

* PR1: Add `sqlglot` dependency and pure extraction/mapping helpers with unit tests.
* PR2: Add database value-probe method and tests using stubs/fakes.
* PR3: Wire `value_validator` into graph retry flow and add graph-level retry tests.
* PR4: Run focused backend tests and document any external DB-dependent failures.

## Out of Scope (explicit)

* Changing the generated SQL execution result schema.
* Full semantic entity linking or vector search for value correction.
* Complex OR trees, function predicates, subquery predicates, and ambiguous aliases in MVP.
* Running destructive SQL or relaxing existing validator rules.

## Technical Notes

* Relevant files: `backend/app/agent/graph.py`, `backend/app/agent/nodes.py`, `backend/app/agent/state.py`, `backend/app/database/executor.py`, `backend/app/rag/schema_models.py`, `backend/pyproject.toml`, backend unit tests.
* sqlglot docs reference: Context7 `/tobymao/sqlglot`; use `parse_one`, `exp.EQ`, `exp.Column`, `exp.Literal`.
