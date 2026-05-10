# Research: runtime probe options

- **Query**: Research implementation options for stage-2 join reliability in SQLAgent, focusing on low-cost online validation for candidate join keys, bounded sample queries, cost control, and relation scoring signals that do not depend on business-table names.
- **Scope**: mixed
- **Date**: 2026-05-10

## Findings

### Files Found

| File Path | Description |
|---|---|
| `backend/app/rag/schema_sync.py` | Builds live relation catalog and already encodes relation precedence plus shared-key inference heuristics. |
| `backend/app/agent/state.py` | Defines stable shared graph contract fields that any stage-2 signal must fit into. |
| `backend/app/agent/graph.py` | Shows current LangGraph node shape and retry routing. |
| `backend/app/agent/nodes.py` | Renders relation guidance into `schema_context` and instructs SQL generation to prefer explicit join hints/confidence. |
| `backend/app/database/executor.py` | Current read-only execution surface; already supports `EXPLAIN`, existence checks, and bounded value suggestions. |
| `backend/app/validator/sql_validator.py` | Enforces read-only `SELECT/WITH`, single-statement, and stable `LIMIT ... ORDER BY` policy. |
| `.trellis/spec/backend/database-guidelines.md` | Repo-level contract for execution path, relation precedence, and graph-shape stability. |
| `backend/tests/unit/test_agent_graph_schema_plan.py` | Tests that relation confidence/hints and `cross_table_diff` already flow into prompt context. |

### Internal Code Patterns

#### 1. Current relation discovery already has a generic, schema-driven scoring shape

`backend/app/rag/schema_sync.py:67-112` defines `_join_column_score()` using only column metadata, not business table names:

- `foreign_key` role: `+5`
- `identifier`: `+2`
- `dimension`: `+1`
- primary key but not generic `id`: `+2`
- suffix `_id`: `+2`
- preferred description tokens like `编号/code/key/number/no`: `+4`
- downrank description tokens like `临时/预/保留/备用/审计/创建/更新/删除`: `-3`
- nullable: `-1`

`backend/app/rag/schema_sync.py:311-417` then applies relation precedence in this order:

1. live foreign keys
2. validated `table_relations.yaml` overrides
3. inferred shared-key relations

This matters for stage-2 because runtime probe signals can be attached as another generic signal source without requiring hard-coded business table pairs.

#### 2. Prompt path already consumes relation confidence/hints

`backend/app/agent/nodes.py:820-867` builds `schema_context` from selected tables plus relation overviews.

`backend/app/agent/nodes.py:876-880` includes a generator rule:

> "JOIN 规则：优先使用 schema_context 中 Relations、Table Relations、hint、confidence 明确推荐的联表键..."

`backend/tests/unit/test_agent_graph_schema_plan.py:286-320` verifies that relation `confidence`, `hint`, and `cross_table_diff` are surfaced in `schema_context`.

This means runtime probe output can map cleanly to relation `confidence`, `join_hint`, or `cross_table_diff`-style metadata instead of requiring graph shape changes.

#### 3. Shared contract is narrow and should remain stable

`backend/app/agent/state.py:6-44` keeps the cross-node contract centered on:

- `schema_catalog`
- `schema_context`
- `generated_sql`
- `validation_error` / `validation_errors`
- `retry_count`
- `rows` / `columns` / `row_count`
- `explanation`
- `status`
- `debug_trace`

`backend/app/agent/graph.py:48-69` shows retry routing depends only on `validation_error` and retry counters. The graph shape is fixed around:

`load_schema_catalog -> intent_parser -> schema_retriever -> sql_generator -> sql_validator -> value_validator -> sql_executor -> result_formatter`

So stage-2 signals fit best as:

- relation metadata injected into `SchemaCatalog.relations`
- text guidance added into `schema_context`
- optional diagnostics inside `debug_trace`
- optional validator-level soft failure via `validation_error` if a candidate join looks unreliable

#### 4. Read-only runtime probing is already compatible with repo policy

`backend/app/validator/sql_validator.py:33-63` allows only single-statement `SELECT` or `WITH` queries.

`backend/app/database/executor.py:35-55` already supports lightweight `EXPLAIN`.

`backend/app/database/executor.py:62-109` already exposes two bounded read-only probe patterns:

- `value_exists()` -> `SELECT 1 ... LIMIT 1`
- `suggest_similar_values()` -> `SELECT DISTINCT ... LIKE ... ORDER BY ... LIMIT N`

This establishes that low-cost probe SQL is consistent with the current execution contract as long as probes stay inside read-only `SELECT/WITH` and bounded result patterns.

### External References

#### A. Execution-feedback and semantic validation are especially useful for join mistakes

- OpenReview paper on SQLENS: https://openreview.net/attachment?id=CusEAujXDm&name=pdf  
  Relevant highlights mention signals for:
  - erroneous join paths
  - empty predicate / empty-result evidence
  - suboptimal join tree detection
  This is useful because stage-2 join reliability is mostly about executable-but-wrong SQL, not syntax errors.

- Survey / evaluation paper: https://arxiv.org/pdf/2604.16493  
  Highlights state the dominant NL2SQL failure mode is semantic mismatch, especially wrong join path or missing bridge table, and recommend execution-result-based semantic validation during generation rather than only after failure.

- Benchmark for semantic NL2SQL errors: https://arxiv.org/pdf/2503.11984  
  Highlights explicitly separate `Join Condition Mismatch` and `Join Type Mismatch` from syntax/runtime errors, supporting the need for join-specific signals.

#### B. Low-cost online validation patterns for candidate join keys

Patterns repeatedly supported by NL2SQL execution-feedback work and standard DB introspection:

1. **Existence probe**  
   Probe whether a candidate key produces any overlap at all.
   Example shape:
   ```sql
   SELECT 1
   FROM A
   JOIN B ON A.k = B.k
   WHERE A.k IS NOT NULL AND B.k IS NOT NULL
   LIMIT 1;
   ```
   Signal: binary viability. Cheap and safe, but too weak alone.

2. **Bounded overlap sample probe**  
   Use a small deterministic sample from one side, then test joinability on the other side.
   Example shape:
   ```sql
   WITH sample_a AS (
     SELECT A.k
     FROM A
     WHERE A.k IS NOT NULL
     ORDER BY A.k
     LIMIT 128
   )
   SELECT
     COUNT(*) AS sample_rows,
     COUNT(B.k) AS matched_rows,
     COUNT(DISTINCT sample_a.k) AS sampled_distinct_keys,
     COUNT(DISTINCT B.k) AS matched_distinct_keys
   FROM sample_a
   LEFT JOIN B ON sample_a.k = B.k;
   ```
   Signals:
   - sampled non-null presence
   - sampled match coverage
   - sampled distinct-key overlap

3. **Directional coverage probe**  
   Sample from both directions separately because many-to-one and one-to-many behave differently.
   Example signals:
   - fraction of sampled foreign-key rows matching a parent row
   - fraction of sampled parent keys referenced by child rows
   This is better than symmetric overlap when relation orientation matters.

4. **Multiplicity probe**  
   Detect whether the join behaves like one-to-one vs one-to-many by sampling keys and counting matches.
   Example shape:
   ```sql
   WITH sample_a AS (
     SELECT A.k
     FROM A
     WHERE A.k IS NOT NULL
     ORDER BY A.k
     LIMIT 64
   )
   SELECT AVG(match_cnt) AS avg_matches
   FROM (
     SELECT sample_a.k, COUNT(*) AS match_cnt
     FROM sample_a
     JOIN B ON sample_a.k = B.k
     GROUP BY sample_a.k
   ) t;
   ```
   Signal: sampled multiplicity; useful for downranking suspicious fan-out joins.

5. **Null-density probe**  
   Estimate whether a candidate join column is mostly unusable due to nulls.
   Example shape:
   ```sql
   SELECT
     COUNT(*) AS n,
     SUM(CASE WHEN k IS NULL THEN 1 ELSE 0 END) AS null_rows
   FROM (
     SELECT k
     FROM A
     ORDER BY k
     LIMIT 256
   ) s;
   ```
   Signal: approximate non-null rate from a bounded sample.

6. **EXPLAIN-only structural probe**  
   Reuse planner estimates without scanning full result sets when the engine provides useful row estimates or join order details.
   PostgreSQL EXPLAIN docs: https://www.postgresql.org/docs/16/sql-explain.html  
   Relevant planner outputs include estimated rows and join strategy. This is weaker than execution sampling but lower cost.

#### C. Estimating non-null rate, coverage, and match quality with bounded samples

Useful signal families:

1. **Approximate non-null rate**  
   Estimate from a fixed small sample `s`:
   - `non_null_rate ~= non_null_count / s`
   Cheap, generic, and table-name-agnostic.

2. **Approximate join coverage**  
   From sampled left keys:
   - `coverage_left_to_right = matched_sample_keys / sampled_non_null_keys`
   This is usually the most interpretable runtime reliability signal for candidate joins.

3. **Approximate reverse coverage**  
   Sample from the opposite endpoint:
   - `coverage_right_to_left`
   Useful to distinguish parent/child orientation and catch incidental same-name overlaps.

4. **Approximate uniqueness / duplication**  
   Compute on a bounded sample:
   - `distinct_rate = COUNT(DISTINCT k) / COUNT(k)`
   - or `avg_matches_per_sample_key`
   If a supposed parent key has high duplicate rate, it is a weaker anchor.

5. **Planner-estimated row sanity**  
   When supported, compare candidate joins by planner-estimated rows or startup cost from `EXPLAIN`, not by executing large joins.

6. **Early-stop probing**  
   Stop after reaching a confidence threshold or after a strict sample cap (e.g. 32/64/128 keys). This follows cost-control logic from approximate query processing and sampling literature.

Supporting DB literature on sampling / bounded approximation:

- Oracle Labs paper on NDV estimation from samples: https://labs.oracle.com/pls/apex/f?p=LABS%3A0%3A104002160510623%3AAPPLICATION_PROCESS%3DGETDOC_INLINE%3A%3A%3ADOC_ID%3A3926  
  Relevant point: distinct-value estimation can be derived from samples when full scans are not feasible.

- Bounded Approximate Query Processing: https://dbgroup.cs.tsinghua.edu.cn/ligl/papers/tkde18-baq.pdf  
  Relevant point: bounded online answers should rely on pre-bounded error/cost envelopes rather than unconstrained scans.

- On random sampling over joins: https://dl.acm.org/doi/10.1145/304182.304206  
  Relevant point: sampling over joins can still be expensive, so join-sample designs should avoid trying to uniformly sample the full join output.

#### D. Cost-control patterns to avoid heavy runtime analysis

1. **Prefer metadata before probes**  
   Use live FK and configured relation hints first; only probe ambiguous candidates. This matches repo precedence in `schema_sync.py:311-417`.

2. **Probe only top-K ambiguous candidates**  
   Run runtime checks only on candidates that are already plausible from schema metadata and shared-key heuristics.

3. **Sample endpoints, not full join outputs**  
   Sample keys from one endpoint table, then test existence/coverage against the other table. This avoids expensive full join sampling.

4. **Use strict caps and deterministic ordering**  
   Keep sample sizes fixed and small (`32`, `64`, `128`, `256`) and include `ORDER BY` because this repo rejects `LIMIT` without stable ordering (`sql_validator.py:52-56`).

5. **Short-circuit on decisive evidence**  
   If null rate is extremely high, or overlap is zero in both directions, the candidate can be downranked without further probing.

6. **Cache probe results per schema scope**  
   Since catalog caching is already scoped by database URL in `.trellis/spec/backend/database-guidelines.md:60-69`, probe summaries can conceptually follow the same schema-scope key.

7. **Separate structure check from data check**  
   First use `EXPLAIN` / metadata for structure, then bounded execution probes only if structure leaves ambiguity.

8. **Avoid coupling probes to business filters**  
   Probe on join-key statistics and overlap only; do not bind the method to domain-specific predicates or table names.

### Mapping to This Repo's Constraints

#### 1. Read-only SQL policy

All candidate probe patterns above can be expressed as single-statement `SELECT`/`WITH` queries, which aligns with:

- `backend/app/validator/sql_validator.py:33-63`
- `.trellis/spec/backend/database-guidelines.md:73-86`

Important repo-specific constraint:

- any probe using `LIMIT` must include deterministic `ORDER BY`, otherwise the current validator rejects it.

#### 2. schema_sync-based relation discovery

The current system already computes relation candidates generically from:

- semantic role
- PK/FK metadata
- nullable
- column naming patterns
- description tokens

Therefore runtime probes should feed relation scoring as additional generic per-relation evidence, such as:

- `probe_non_null_rate`
- `probe_coverage_forward`
- `probe_coverage_reverse`
- `probe_avg_multiplicity`
- `probe_status` / `probe_sample_size`

These signal names are column/relation-oriented, not table-name-oriented, so they preserve database-switching behavior required by `.trellis/spec/backend/database-guidelines.md:197-203`.

#### 3. Graph/shared contract stability

Given `AgentState` and graph routing, the least disruptive fit is:

- enrich `SchemaCatalog.relations` with runtime reliability metadata
- render a concise textual summary into `schema_context`
- place raw numbers into `debug_trace`
- only use `validation_error` when a candidate join is so weak that generation should retry

This keeps the existing node sequence and API response contract intact while still allowing stage-2 reliability evidence to influence SQL generation.

#### 4. Relation scoring without business-table-name coupling

The repo already demonstrates the right abstraction level in `_join_column_score()` by scoring on metadata properties rather than on explicit table pairs.

A runtime analogue should stay at the same abstraction level, using only generic statistics such as:

- nullability / sampled non-null rate
- overlap rate between endpoints
- directional coverage
- duplicate intensity / fan-out
- planner-estimated join cardinality or startup cost
- presence/absence of any match

Those signals can be fused with existing relation types:

- `foreign_key` remains strongest default prior
- configured relation remains next
- inferred shared-key candidates benefit most from runtime probes

### Caveats / Not Found

- No existing repo file currently implements runtime join-key probes or persists data-quality evidence on relations; current relation scoring is metadata-only.
- The current `SQLExecutor` exposes `EXPLAIN`, `value_exists`, and suggestion helpers, but not a dedicated bounded join-probe helper.
- The repo contracts emphasize graph-shape stability, so any stage-2 implementation should preserve existing state fields or confine additions to optional metadata/debug payloads.
