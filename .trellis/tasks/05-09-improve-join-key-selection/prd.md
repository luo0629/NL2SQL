# brainstorm: improve join key selection

## Goal

改进 SQLAgent 在多表联查时的 join key 选择能力：当多个表存在同名字段，但其中部分字段已废弃、值大面积为空、或并非真实业务关联键时，系统应尽量选择更可靠的关联字段，避免生成错误 JOIN；同时不采用把所有同名字段都用 OR 拼接兜底的方式。

## What I already know

* 用户反馈：数据库中存在多个同名字段，但只有其中一个适合联表；如果误选空字段/废弃字段会导致查询失败。
* 用户明确不接受通过 `A=A1 OR B=B1` 这类并列条件把所有同名字段都拼起来规避问题。
* 当前 agent 在 `backend/app/agent/nodes.py` 的 `schema_retriever` 中，把 relation 概览和表 schema 文本拼成 `schema_context` 交给模型。
* 当前 relation 文本来源于 `table_relations.yaml`，渲染逻辑在 `backend/app/agent/nodes.py` 的 `_build_table_relations_overview`。
* 自动生成 relation 的逻辑在 `backend/app/config_generation.py` 的 `_build_relations_payload`，目前主要依赖主键/外键和同名字段描述，缺少“这个字段更可靠”的显式排序或质量指标。
* `SchemaRelation` 已经支持 `confidence` 和 `join_hint` 字段，但当前测试表明默认自动生成配置下，这两个 enrichment 为空。
* `SchemaColumn` 当前只保留数据类型、可空、主键、描述、业务术语、语义角色，不包含非空率、distinct 比例、覆盖率等数据质量统计。
* `inspect_live_schema` 当前只采集列定义、主键、外键、索引、注释，不做数据采样，因此没有现成的“空值率/唯一性”依据可直接用于 join key 打分。

## Assumptions (temporary)

* 问题主要出在 join candidate 排序/提示不足，而不是 SQL 语法生成器完全不知道要联哪两个表。
* 当前最小可行解可以优先落在 schema 同步/配置生成/提示词增强链路，而不必一开始就做复杂的运行时 SQL 重写。
* 如果需要利用真实数据质量信号，可能要在 schema 巡检阶段增加可选的数据 profiling 或采样逻辑。

## Open Questions

* （当前无阻塞问题）

## Requirements

* 采用混合策略：人工 override 为最高优先级，缺省时再走自动评分。
* 第一版人工 override 继续复用 `table_relations.yaml` 链路扩展字段，不新增独立 join policy 文件。
* 第一版自动评分只基于元数据，不直接读取真实业务数据做 profiling。
* 第一版 MVP 只做“推荐 join key”链路，不在本轮引入 disabled / discouraged candidates。
* 系统需要在多个潜在同名字段中更稳定地选出一个推荐 join key。
* 不能通过多个等值条件 OR 拼接来兜底。
* 现有 relation/schema_context 输出链路应继续可用于 LLM 生成 SQL。
* 自动评分结果需要能以推荐 join key / confidence / join hint 形式进入 relation 或 schema_context。
* 方案应尽量与当前项目从 skeleton 向企业级 SQLAgent 演进的方向一致。

## Acceptance Criteria

* [ ] 当两张表存在多个同名或相近字段时，schema_context 能明确给出推荐 join key 或足够强的 join guidance。
* [ ] SQL 生成阶段不会再默认把所有同名字段都并列为 OR 条件。
* [ ] `table_relations.yaml` 可承载人工 override，并且 override 优先于自动评分结果。
* [ ] 至少新增/更新一组测试覆盖“同名字段但只有一个字段适合联表”的场景。
* [ ] 现有 relation/schema context 行为未被破坏。

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Technical Approach

* 采用混合策略：人工 override 作为最高优先级，缺省时使用元数据自动评分。
* 第一版自动评分仅使用已有元数据与可推导信号（如主键/外键、索引、可空、命名/语义），不读取真实数据做 profiling。
* relation payload / enrichment 需要能产出“推荐 join key”及其可信度、提示信息，并在 `schema_context` 中显式前置给 LLM。
* 本轮不实现 disabled / discouraged candidates，也不做运行时 SQL 重写。

## Decision (ADR-lite)

**Context**: 当前 SQLAgent 在多表联查场景下，可能因同名字段冲突而选到空字段、废弃字段或弱关联字段，导致错误 JOIN；现有自动 relation 生成链路缺少“哪个键更可靠”的结构化表达。

**Decision**: 本轮采用保守版混合策略 MVP：在 `table_relations.yaml` 上扩展人工 override 能力，同时增加基于元数据的轻量自动评分，最终向 LLM 提供单一推荐 join key + confidence + join hint。

**Consequences**: 该方案改动小、兼容现有链路、可快速验证效果；但第一版不会显式输出禁用候选键，也不会利用真实数据质量信号，后续若老库场景复杂仍可能需要 profiling 或更强的关系治理。

## Out of Scope

* 通过生成超宽松 OR JOIN 条件来掩盖 join key 不确定性
* disabled / discouraged candidates 的完整输出机制
* 一次性做全量数据血缘/血统治理平台
* 在本轮直接重写整个 agent graph
* 基于真实业务数据的 profiling / 采样评分

## Technical Notes

* 关键文件：
  * `backend/app/agent/nodes.py`
  * `backend/app/config_generation.py`
  * `backend/app/rag/schema_models.py`
  * `backend/app/rag/schema_introspection.py`
  * `backend/app/rag/schema_enrichment.py`
  * `backend/tests/unit/test_agent_graph_schema_plan.py`
  * `backend/tests/unit/test_schema_enrichment.py`
* 当前最可能的落点：
  * relation payload 中增加推荐 join key 与可信度说明
  * `table_relations.yaml` 扩展 override 字段以承载推荐关系
  * `schema_context` 文本中把“推荐关联字段”显式前置
  * 后续如需要更强自动判定，再扩展 schema introspection 或 sync，增加轻量 profiling

## Feasible approaches here

**Approach A: 显式配置优先**

* How it works: 在 `table_relations.yaml` / relation enrichment 中显式标注推荐 join key、禁用字段或优先级，LLM 只按推荐字段联表。
* Pros: 最稳定、最可控、最容易快速落地。
* Cons: 需要人工维护配置；自动化程度较低。

**Approach B: 自动数据画像优先**

* How it works: 在 schema 巡检/同步阶段对候选字段做非空率、唯一性、索引/外键命中等评分，自动为 relation 产出推荐键。
* Pros: 更智能，适合复杂老库。
* Cons: 需要额外查询数据库，成本/复杂度更高，也要处理采样准确性。

**Approach C: 混合策略（推荐，已选）**

* How it works: 先支持显式 override 作为最高优先级，同时增加轻量自动评分（外键、主键、非空、索引、命名语义），把结果写进 relation confidence/join_hint；未来可再接可选 profiling。
* Pros: 兼顾可控性和可扩展性，适合当前仓库演进路线。
* Cons: 实现比纯配置略复杂，评分规则需要测试约束。
