# brainstorm: stage 2 join reliability

## Goal

继续推进 SQLAgent 联表可靠性的第二阶段能力，在第一阶段“FK 优先 + 配置覆盖 + 自动推断共享业务键 + cross_table_diff / hint / confidence 注入”的基础上，引入更强的 join key ranking / relation scoring 与半自动关系验证机制，进一步降低高空值、低覆盖、脏字段、临时字段被错误选作联表键的概率，同时保持对数据库切换的自动适应能力，而不是重新引入业务表硬编码。

## What I already know

* Stage 1 已完成，并已从 `docs/NL2SQL_AGENT_IMPLEMENTATION_TODO.md` 明确为已落地。
* 当前 `backend/app/rag/schema_sync.py` 已实现：真实 FK、配置覆盖、同名字段自动推断共享业务键。
* 当前 `_join_column_score()` 仍主要是基于字段名、描述、semantic_role、nullable 的静态启发式评分。
* 当前 `schema_context` 与 SQL prompt 已能注入 `cross_table_diff`、`hint`、`confidence`，但这些信号还不是基于真实样本覆盖率或值分布验证得出的。
* 用户明确要求第二阶段考虑：join key ranking / relation scoring、基于样本值或覆盖率的半自动关系验证、以及对高空值/低覆盖/脏字段进一步降权。
* 用户同时要求保留数据库无关性：修改数据库链接和目标表后，系统应自动识别，而非依赖业务硬编码。

## Assumptions (temporary)

* 第二阶段本轮应以“最小可行增强”为目标，而不是一次性做成完整离线 relation governance 系统。
* 如果采用在线轻量验证，应限制在 schema retrieval / relation ranking 前后的低成本探测，不能把每次查询都变成重型分析作业。
* 如果采用离线统计缓存，应尽量复用现有 schema sync / config generation 路径，而不是新造一套完全平行的数据质量系统。

## Open Questions

* 暂无。

## Requirements (evolving)

* 保持数据库切换自适应，不引入新的业务表硬编码。
* 在现有 Stage 1 基础上增强 join key ranking / relation scoring。
* 对高空值、低覆盖、脏字段、临时字段进一步降权。
* 本轮 MVP 采用在线轻量验证：在 join relation 候选排序阶段增加低成本样本探测，如非空率、有限覆盖率或有限匹配率，但避免把每次查询变成重型分析任务。
* 保持现有 graph 主链路和 shared contract 稳定，不随意扩大 API 面。

## Acceptance Criteria (evolving)

* [ ] 明确 Stage 2 本轮 MVP 的在线轻量验证边界。
* [ ] 明确 relation scoring 增强方案及其输入信号来源。
* [ ] 明确哪些字段/关系会被进一步降权。
* [ ] 方案保持对数据库切换的自动适应，不依赖业务表名硬编码。
* [ ] 在线验证设计有明确的成本控制，不会把每次请求放大成重型统计作业。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 本轮不一次性实现完整企业级 relation governance 平台。
* 本轮不优先处理多智能体拆分。
* 本轮不改变现有 API 契约，除非明确需要并同步前端。

## Technical Notes

* Stage roadmap: `docs/NL2SQL_AGENT_IMPLEMENTATION_TODO.md`
* Current inferred relation logic: `backend/app/rag/schema_sync.py`
* Current prompt constraints: `backend/app/agent/nodes.py:_build_sql_generation_prompt`
* Likely impacted modules: `backend/app/rag/schema_sync.py`, `backend/app/config_generation.py`, `backend/app/services/rag_service.py`, `backend/app/agent/nodes.py`, relevant unit tests
