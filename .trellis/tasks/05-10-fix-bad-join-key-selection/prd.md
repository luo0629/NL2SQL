# brainstorm: fix persistent bad join-key selection

## Goal

修复当前 SQLAgent 仍然持续存在的联表字段选择错误问题：当候选联表字段中存在高空值、低覆盖、疑似废弃字段时，系统仍可能选中它们，导致生成 SQL 结果为空；而改用其他字段联表则能查出结果。本轮目标是找出这个问题在当前主路径中的真实断点，并做一个能稳定降低空结果误选的 MVP 修复。

## What I already know

* 用户明确反馈：这个问题在当前项目里仍然持续存在。
* Stage 1 已完成：FK 优先、配置覆盖、自动推断共享业务键、cross_table_diff / hint / confidence 注入。
* Stage 2 已完成：metadata-first candidate filtering、bounded runtime probes、relation scoring feedback。
* Stage 3 已完成：schema governance graph artifacts、离线关系质量产物，并挂载到 `SchemaCatalog.relationship_graph`。
* 当前代码中，`backend/app/agent/nodes.py` 的 SQL prompt 已经消费 `Relations / hint / confidence / score / validation_summary` 等文本信号。
* 当前 `backend/app/rag/schema_sync.py` 会生成 `catalog.relationship_graph`，但从现有主路径 grep 结果看，Agent 主要仍然通过 `catalog.relations` 和 `schema_context` 消费关系信息，而不是直接做 graph-driven join path selection。
* 当前测试重点仍集中在 `schema_context` 是否暴露了这些信号，而不是“错误联表被主路径可靠纠正”。

## Assumptions (temporary)

* 当前问题更可能是 Stage 2/3 的信号没有真正改变主路径候选优先级，或者没有在生成/校验阶段形成强约束。
* 一个现实 MVP 不一定要立刻把 Agent 全量切到 graph retrieval，也可能先修 relation ranking 的消费顺序、阈值、回退重试逻辑。
* 如果当前生成链路在错误 join 后没有明确回退或重选机制，那么即使 graph/metrics 已存在，也可能继续“知道但没用上”。

## Open Questions

* 本轮 MVP 你更希望优先修哪一层？

## Requirements (evolving)

* 修复当前仍会选择高空值/低覆盖/疑似废弃字段作为 join key 的问题。
* 修复方案必须基于当前已落地的 Stage 1/2/3 能力，而不是重新引入业务表硬编码。
* 优先找出是主路径未消费 graph/metrics，还是 relation ranking / prompt / retry 阈值有问题。
* 最终要让系统在“错误 join 导致空结果，但存在更优替代 join key”时更倾向于选中可查询出结果的那条关系。

## Acceptance Criteria (evolving)

* [ ] 明确当前问题的主断点位置。
* [ ] 修复后，错误 join 候选更容易被降权或被替代候选覆盖。
* [ ] 修复方案不依赖新的业务表硬编码。
* [ ] 增加可复现并能锁住该问题的测试。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 本轮不一次性重构全部 Agent 主链路为完整 graph retrieval。
* 本轮不优先处理 README 或文档重写。
* 本轮不做新的多智能体拆分。

## Technical Notes

* Prompt consumer: `backend/app/agent/nodes.py`
* Relation generation and ranking: `backend/app/rag/schema_sync.py`
* Governance artifact generation: `backend/app/rag/schema_governance.py`
* Shared relation model: `backend/app/rag/schema_models.py`
* Current tests touching relation signals: `backend/tests/unit/test_agent_graph_schema_plan.py`, `backend/tests/unit/test_schema_sync.py`, `backend/tests/unit/test_schema_governance.py`
