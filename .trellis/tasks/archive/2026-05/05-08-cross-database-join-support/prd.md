# brainstorm: cross database join support

## Goal

Extend the NL2SQL agent from a single configured MySQL database to multiple databases on the same MySQL instance, so schema retrieval, SQL generation, validation, and execution can support cross-database JOIN SQL using fully qualified database.table references.

## What I already know

* Current configuration uses one `DATABASE_URL` pointing at a MySQL database.
* The new requirement is to support two databases on the same MySQL instance: `jc_config` and `jc_experimental`.
* Host, port, username, and password are shared; only database names differ.
* The user wants configuration simplified to a list of database names rather than separate full URLs per database.
* The Agent must understand schemas from both databases and generate cross-database JOIN SQL.
* Do not persist or expose database credentials in task docs or prompts.

## Assumptions (temporary)

* Keep one base `DATABASE_URL` for connection credentials and default/current compatibility, then add a list of database names for schema scope.
* SQL should use MySQL fully qualified identifiers like `database`.`table`.`column` or `database`.`table` where needed.
* The first database in the configured list can be the default database for unqualified legacy SQL.
* MVP targets databases on the same MySQL server only, not multiple hosts or different credentials.

## What I discovered from repo/context

* `backend/app/config.py::Settings` currently has a single `database_url`.
* `backend/app/database/engine.py` creates one global async engine from `settings.database_url`.
* `backend/app/rag/schema_sync.py::sync_schema_metadata` introspects only `connection.engine.url.database` using SQLAlchemy inspector.
* `SchemaTable` and `SchemaRelation` currently store only table names, not database/schema names.
* `backend/app/agent/nodes.py::_format_table_schema` renders `Table `table`` and relations as `table.column -> table.column`.
* `schema_retriever`, fallback SQL, semantic rendering, and value validation match tables by `table.name`.
* `SQLValidator` is read-only oriented and does not currently reject qualified MySQL names; cross-database syntax should mostly be compatible.
* Existing business semantic YAML uses `database_url` fingerprinting; multi-database config must keep cache/YAML identity deterministic without exposing credentials.

## Open Questions

* None blocking.

## Requirements

* Add multi-database config using one shared connection and a list of database names.
* Preserve compatibility with existing single `DATABASE_URL` behavior where possible.
* Schema sync must load tables/columns/relations from all configured databases on the same MySQL instance.
* Schema model must carry database/schema identity for tables and relations.
* Schema context must expose database-qualified table identities to the LLM.
* SQL generation prompt must instruct cross-database SQL to use fully qualified MySQL identifiers such as `db`.`table`.
* SQL generation must still restrict tables/fields to schema_context.
* Fallback SQL should emit database-qualified table names when table metadata has a database name.
* SQL validation/execution should support read-only cross-database SELECT/WITH SQL on the same MySQL instance.
* Value existence validation should understand database-qualified table references if applicable.
* Cache keys and business semantic YAML identity must include the configured database list.

## Acceptance Criteria (evolving)

* [ ] Config can represent `jc_config` and `jc_experimental` with shared connection credentials.
* [ ] Single `DATABASE_URL` behavior remains valid when no database list is configured.
* [ ] Schema catalog includes table metadata from both configured databases.
* [ ] Duplicate table names across databases are distinguishable.
* [ ] Prompt/schema context makes database-qualified table names visible and usable.
* [ ] Agent can generate SQL with cross-database qualified joins.
* [ ] Fallback SQL uses qualified table names when needed.
* [ ] SQL validation accepts safe read-only cross-database SELECT/WITH SQL.
* [ ] Value validation can map `db.table` or aliased table references.
* [ ] Existing single-database tests remain compatible.
* [ ] Focused tests cover multi-database config, schema rendering, and qualified SQL behavior.

## Definition of Done

* Tests added/updated for config parsing, schema catalog/rendering, SQL generation prompt expectations, and validation/execution compatibility.
* Relevant backend tests pass or external DB-dependent failures are documented.
* No frontend changes unless needed for selecting/indicating database scope.
* No credentials committed to docs or tests.

## Out of Scope (explicit)

* Multiple MySQL hosts or per-database credentials.
* Cross-engine joins.
* User-supplied database URLs.
* Permission management beyond existing read-only DB user and SQLValidator boundaries.

## Expansion Sweep

### Future evolution

* Later versions may support named data sources across different MySQL hosts or credentials, but MVP stays same-instance only.
* Later versions may add UI/API database scope selection, but current request is cross-database JOIN in one agent scope.

### Related scenarios

* Schema enrichment, business semantics, enum mappings, and value validation must all use the same database-qualified table identity.
* Existing single-database behavior should continue for local SQLite/mock tests and legacy `.env` files.

### Failure & edge cases

* Duplicate table names across databases must not collide in retrieval, relation rendering, value validation, or prompt instructions.
* If a configured database name is unavailable or unauthorized, schema sync should fail safely with sanitized errors.

## Feasible Approaches

### Approach A: Base URL + database list, qualified schema identities (Recommended)

* How it works: keep `DATABASE_URL` as the shared connection/default database, add `DATABASE_NAMES=jc_config,jc_experimental`, introspect all listed databases, and render tables as `db`.`table`.
* Pros: matches same-instance requirement, minimal credential duplication, preserves current `.env` compatibility.
* Cons: requires schema model/rendering changes to carry database identity.

### Approach B: Multiple full database URLs

* How it works: configure `DATABASE_URLS` or `DATABASE_URL_MAIN` / `DATABASE_URL_OTHER` with full URLs.
* Pros: more general for future multi-host support.
* Cons: duplicates credentials, contrary to user's simplified config preference, larger engine-management change.

### Approach C: Keep schema single-db but let generated SQL manually reference another db

* How it works: only prompt the LLM to use a second database name without syncing its schema.
* Pros: fastest change.
* Cons: unsafe and brittle; the Agent cannot understand or validate the second database schema.

## Decision (ADR-lite)

**Context**: The Agent needs to reason over two databases on the same MySQL instance and produce cross-database JOIN SQL without duplicating credentials or accepting user-supplied connection strings.

**Decision**: Use Approach A. Keep one shared `DATABASE_URL` for host/port/user/password and default database compatibility, add `DATABASE_NAMES=jc_config,jc_experimental`, and make schema catalog plus prompt rendering database-qualified.

**Consequences**: This preserves simple configuration and single-instance execution, but requires schema model, schema sync, relation rendering, fallback SQL, and value validation to understand canonical `database.table` identities.

## Technical Approach

* Add `Settings.database_names` parsing from a comma-separated env value.
* Derive the effective database list from `database_names` when set, otherwise fall back to the database in `database_url`.
* Extend schema models with database/schema identity while keeping existing table name compatibility.
* Update schema sync to introspect each configured database on the same engine/connection.
* Render schema context with fully qualified MySQL names like `jc_config`.`table` and `jc_experimental`.`table`.
* Update SQL generation prompt to require qualified names for cross-database queries and still restrict all identifiers to schema_context.
* Update fallback SQL and relation rendering to use qualified table names when available.
* Update value validation mapping so `db.table`, table aliases, and unambiguous unqualified tables resolve correctly.
* Keep single-database mode compatible for existing tests and local SQLite/mock setups.

## Implementation Plan

* PR1: Config and schema model support for database list plus qualified table identity.
* PR2: Multi-database schema sync and relation construction.
* PR3: Schema context, SQL prompt, fallback SQL, and value validation qualified-name support.
* PR4: Focused tests for config parsing, multi-db schema rendering, qualified fallback SQL, and value validation mapping.

## Technical Notes

* Relevant files inspected: `backend/app/config.py`, `backend/app/database/engine.py`, `backend/app/rag/schema_sync.py`, `backend/app/rag/schema_models.py`, `backend/app/services/rag_service.py`, `backend/app/agent/nodes.py`, `backend/app/agent/value_validation.py`, `backend/app/validator/sql_validator.py`.
* Main structural issue: current table identity is only `table.name`; cross-database support needs a canonical qualified identity while preserving table display and backwards compatibility.
