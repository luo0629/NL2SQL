# Conversational Enum Mappings

## Goal

Add conversational enum value mappings for enum-like database fields so natural-language phrases such as “待支付”, “未支付”, and “已支付” can be mapped to real database values like `1` and `2`. When schema context is dynamically rendered for SQL generation, enum comparison information should be appended to the relevant field descriptions so the LLM can directly see mappings like `待支付/未支付=1, 已支付=2`.

## What I already know

* The project now uses a six-node NL2SQL graph: `intent_parser -> schema_retriever -> sql_generator -> sql_validator -> sql_executor -> result_formatter`.
* Retired `SemanticQuery`, `sql_plan`, `schema_linking`, `value_linking`, and `join_path` pipelines must not be reintroduced.
* A business semantic layer already exists and can derive/merge schema-driven semantics and YAML overrides.
* The enum mapping should reuse the business semantic layer, SchemaCatalog, schema sync/schema models, RagService, SQLValidator, and SQLExecutor.
* User wants enum comparison info appended during dynamic schema rendering, not hidden only in debug.

## Assumptions (temporary)

* MVP should support enum mappings from schema comments and YAML/generated business semantics.
* Enum mappings should enrich field descriptions and SQL generation prompt, not change API response shape.
* Enum mapping values should be validated as safe scalar values before prompt injection.

## Open Questions

* Current no blocking questions.

## Requirements

* Detect enum-like fields from schema metadata, comments, and business semantic YAML/generated content.
* Use automatic + YAML sources: auto-extract enum values/labels from field comments, then let YAML overrides add conversational aliases such as `未支付`, `待付款`, `已付款`.
* Build mappings from conversational aliases to real DB values, e.g. `待支付/未支付=1`, `已支付=2`.
* Append enum comparison info to relevant field descriptions/schema context used by `sql_generator`.
* Make enum mapping visible to `intent_parser` enough to improve relevant table selection.
* Validate enum mapping table/column references against the live schema catalog.
* Reject or diagnose unsafe enum values or fragments; enum mappings must not inject arbitrary SQL.
* Preserve six-node graph shape and current API response compatibility.

## Acceptance Criteria (evolving)

* [ ] Schema context for enum-like fields includes human phrase to DB value mapping.
* [ ] Conversational aliases can come from auto-extracted comments and YAML overrides.
* [ ] Invalid enum mapping references are filtered or reported as diagnostics.
* [ ] SQL generation prompt can see enum mappings next to field descriptions.
* [ ] Tests cover comment-derived enum extraction, YAML override enum mapping, invalid references, and schema context rendering.

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Backend focused or full tests pass
* API response compatibility considered
* Specs updated if new executable contracts are established
* Rollout/rollback considered because enum mappings affect SQL filter correctness

## Technical Approach

* Reuse `BusinessEnum` in the business semantic layer as the source of enum mappings.
* Normalize enum mappings into a prompt-safe display format: `<alias1>/<alias2>=<db_value>` grouped by field.
* Auto-extracted enum labels become default aliases; YAML overrides can add more conversational aliases per value.
* Append enum mapping text next to matching field descriptions in schema context, so SQL generation sees it in the same place as the column definition.
* Also keep concise enum mapping lines in semantic context for intent/table selection and SQL prompt reinforcement.
* Validate enum override table/column references and scalar values; reject unsafe SQL fragments or non-scalar enum values.
* Keep the current six-node graph unchanged and preserve API response shape.

## Decision (ADR-lite)

**Context**: LLM currently sees enum fields but not always the natural-language phrase to real database value mapping near the column definition. This causes filters like “未支付订单” or “已支付订单” to map unreliably.

**Decision**: Use an automatic + YAML approach. Column comments provide base enum labels and values; YAML overrides add conversational aliases. Render normalized mappings directly beside field descriptions in schema context.

**Consequences**: The LLM gets direct enum comparison hints during SQL generation, while teams can improve aliases without code changes. The trade-off is that enum parsing and override validation need tests to prevent unsafe or stale mappings.

## Out of Scope

* No UI/admin console for enum management.
* No vector retrieval or embedding matching.
* No reintroduction of retired NL2SQL planning/linking pipeline.
* No database writes.
* No automatic user-feedback learning for enum aliases.

## Implementation Plan (small PRs)

* PR1: Normalize `BusinessEnum` values and aliases into field-level conversational mapping strings.
* PR2: Append enum mapping text to schema context field descriptions and semantic context.
* PR3: Extend YAML enum override validation and tests for conversational aliases.
* PR4: Update specs for enum mapping contract.

## Technical Notes

* `schema_sync.py` reads DB column comments and merges fallback descriptions into `SchemaColumn.description`.
* `business_semantics.py::_extract_enum_values()` already extracts simple enum-like patterns from column descriptions and `derive_business_semantics()` builds `BusinessEnum(name=table.column, values={code: label}, aliases=...)`.
* YAML overrides already support `overrides.enums.<name> = {table, column, values, aliases}` and merge labels/aliases into semantic terms.
* `schema_models.py` defines `BusinessEnum{name, table, column, values, aliases, source}`.
* `rag_service.py::_render_table_context()` already includes enum information in table context.
* `agent/nodes.py::schema_retriever()` produces `schema_context` and `semantic_context`; `_render_semantic_context()` has a `Business enums:` section consumed by SQL prompt.
* Gap: enum output is not normalized into field-adjacent conversational mappings like `待支付/未支付=1, 已支付=2`, and field descriptions do not consistently include that mapping.
