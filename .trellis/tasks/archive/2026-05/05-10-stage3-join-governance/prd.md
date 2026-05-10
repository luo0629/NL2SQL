# brainstorm: stage 3 join governance

## Goal

继续推进 SQLAgent 联表可靠性的第三阶段能力，把当前以启发式关系发现、轻量运行时验证为主的方案，升级到面向企业级治理的 schema graph / relationship graph 体系：定期离线统计列质量、join 覆盖率、废弃字段状态，并让 Agent 基于图检索和图约束来选择 join path，而不是继续主要依赖字符串同名推断与局部探测。

## What I already know

* Stage 1 已完成：FK 优先、配置覆盖、自动推断共享业务键、cross_table_diff / hint / confidence 注入。
* Stage 2 已完成：metadata-first candidate filtering、bounded runtime probes、relation scoring feedback。
* 当前 `SchemaRelation` 已包含 `confidence`、`ranking_score`、`validation_summary` 等信号。
* 当前 `schema_sync.py` 已支持从 live schema 构建关系候选，并对候选 join key 做静态+在线轻量验证。
* 当前 `Agent` 仍主要通过 `schema_context` 文本和 relation hints 影响 SQL 生成，而不是基于正式的 relationship graph 检索 join path。
* 当前 roadmap 中 Stage 3 方向已明确包括：relation governance、更严格的多跳 join path selection、联表质量指标、观测、回归与审计闭环。
* 用户明确希望第三阶段完成：schema graph / relationship graph、定期离线统计列质量、join 覆盖率、废弃字段状态，并让 Agent 基于图检索而不是基于字符串猜 join。

## Assumptions (temporary)

* 第三阶段本轮不太可能一次性把“图模型 + 离线统计 + 图驱动 Agent 检索 + 完整观测审计平台”全部做完。
* 最自然的 MVP 分叉点是：先落图模型与离线统计产物，还是同时把 Agent 检索主路径切到图驱动。
* 为了保持现有 LangGraph 主链路稳定，第三阶段最好优先把图模型作为 schema retrieval 的新基础设施，而不是大改 graph 形状。

## Open Questions

* 这轮 graph / 指标文件的更新触发方式，应该优先挂在哪条链路上？

## Requirements (evolving)

* 建立 schema graph / relationship graph 作为关系检索和 join path selection 的基础。
* 支持定期离线统计至少以下质量信号：列质量、join 覆盖率、废弃字段状态。
* 本轮 MVP 的第一优先级是统一 graph 数据结构与 relation 质量指标产物，而不是先做多跳路径控制或接口层扩展。
* 本轮只生成可落盘、可缓存、可被后续 Agent 消费的 graph / metrics 产物文件，不强求同时完成内部读取服务。
* 本轮 MVP 优先完成图模型与离线统计产物，Agent 暂不直接切主检索链路到 graph retrieval，而是继续消费增强后的 schema / relation 产物。
* 为后续 Agent 图驱动检索保留明确的数据模型与接入点。
* 保持数据库切换自适应，不引入新的业务表硬编码。
* 尽量保持现有 LangGraph 主链路稳定，避免无必要的大改图结构。

## Acceptance Criteria (evolving)

* [ ] 明确第三阶段本轮 MVP 的主落点。
* [ ] 明确 schema graph / relationship graph 的最小数据模型。
* [ ] 明确离线统计产物的范围与更新方式。
* [ ] 本轮优先产出统一 graph 数据结构与 relation 质量指标文件。
* [ ] 本轮不直接切 Agent 主检索链路到图驱动，但为后续切换保留清晰接入点。
* [ ] 图模型与离线统计设计保持数据库无关，不依赖业务表硬编码。
* [ ] 产物文件具备清晰的更新触发方式，而不是只能靠手工维护。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 本轮不一次性做完整企业级 observability / audit 平台。
* 本轮不处理多智能体拆分。
* 本轮不为了图检索而随意破坏现有 graph 主链路合同。

## Technical Notes

* Roadmap: `docs/NL2SQL_AGENT_IMPLEMENTATION_TODO.md`
* Current relation model: `backend/app/rag/schema_models.py`
* Current relation discovery and runtime validation: `backend/app/rag/schema_sync.py`
* Current prompt-driven relation consumption: `backend/app/agent/nodes.py`
* Likely impacted modules: `backend/app/rag/schema_models.py`, `backend/app/rag/schema_sync.py`, `backend/app/services/rag_service.py`, `backend/app/agent/nodes.py`, possibly config generation and new offline stats artifacts
