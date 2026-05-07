# brainstorm: 通用企业级 NL2SQL 链路

## Goal

将 SQLAgent 从依赖不稳定 `sql_plan` 渲染 SQL 的测试型链路，改造成更通用、可切换数据库连接、可逐步面向生产数据库的企业级 NL2SQL 链路。目标是在当前真实测试数据库可用的基础上，提高自然语言与 SQL 的语义匹配度，并建立清晰的 schema grounding、SQL 安全校验和执行边界。

## What I already know

* 用户已经接入真实数据库，但目前主要用于测试。
* 用户希望后续只替换数据库连接即可指向生产数据库。
* 当前痛点是自然语言与最终 SQL 不匹配，`sql_plan` 渲染 SQL 的方式不稳定且正确率低。
* 项目目标是从教学型 NL2SQL skeleton 演进为企业级 SQLAgent。
* 当前优先级应聚焦真实 SQL 执行、schema 检索、SQL 安全边界、观测与回归覆盖。

## Assumptions (temporary)

* 测试数据库和生产数据库会尽量保持同构或至少具备可同步的 schema 元数据。
* 本任务优先改造后端 NL2SQL 链路，前端只在 API 响应字段变化时做必要适配。
* 第一阶段不追求覆盖所有复杂 SQL，而是提高单库、多表、过滤、聚合、排序、limit 等常见查询的稳定性。

## Open Questions

* 当前无阻塞问题。

## Requirements

* MVP 范围为“生成+执行”：稳定生成 SQL 的同时完善真实执行结果返回、错误反馈和有限修复闭环。
* 替代以 `sql_plan` 直接渲染 SQL 的主路径。
* 引入更稳定的语义中间表示，例如 `SemanticQuery`，承载 intent、metrics、dimensions、filters、time_range、joins、order_by、limit 等语义信息。
* 引入 schema grounding，将用户词汇绑定到真实表、字段、关系或业务指标。
* 保留测试数据库连接，并为生产数据库替换连接提供清晰配置边界。
* SQL 执行必须具备只读、安全、limit、超时和错误处理边界。
* 默认采用阈值执行：只有 schema grounding / join planning / semantic query 置信度达到阈值才自动执行；低置信度返回 SQL 草案、解释和澄清提示，不查询数据库。
* 执行失败时应将数据库错误转为受控反馈，并允许最多 1–2 次 SQL 修正，不泄露敏感连接信息。

## Acceptance Criteria (evolving)

* [ ] 相同自然语言问题生成的 SQL 与真实 schema 字段、表关系匹配。
* [ ] SQL 生成链路不再主要依赖 `sql_plan` 模板渲染。
* [ ] 通过校验的 SQL 能在测试数据库执行并返回 `rows`、`columns`、`row_count`、`execution_summary`。
* [ ] 数据库执行错误会进入受控修正或受控失败路径，而不是直接返回不稳定 SQL。
* [ ] 测试数据库连接可通过配置替换为生产数据库连接。
* [ ] 危险 SQL 被阻止，查询执行默认受安全边界限制。
* [ ] 关键链路有单元或集成测试覆盖。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Technical Approach

* 新增或收敛 `SemanticQuery` 作为主语义中间表示，表达用户意图、实体、指标、维度、过滤条件、时间范围、排序、limit 与置信度。
* 将现有 `query_understanding`、`schema_linking`、`value_links`、`join_path_plan` 和 `business_semantic_brief` 汇总为 `SemanticQuery`，再由 SQL 生成链路消费。
* 将 `sql_plan` 降级为兼容层或调试信息；短期可由 `SemanticQuery` 编译出 SQL plan，长期再替换为更强 SQL AST / dialect builder。
* 在执行前增加执行门控：只读校验 + schema/plan 一致性校验 + 置信度阈值 + limit/timeout 边界。
* 对执行错误做受控修复：数据库错误 → 修复上下文 → 最多 1–2 次重试 → 仍失败则返回可解释错误。
* 保持 `database_url` 配置切换路径，避免把测试库细节写死到生成逻辑里。

## Decision (ADR-lite)

**Context**: 现有链路已经接入真实执行，但 SQL 仍主要由 `sql_plan` 渲染，导致自然语言与最终 SQL 容易错配。

**Decision**: MVP 选择“生成+执行”，并采用“阈值执行”策略：高置信度 SQL 自动执行，低置信度不查库而返回澄清/解释。

**Consequences**: 该方案比只生成 SQL 更能验证真实结果，比总是执行更安全；代价是需要维护置信度计算和低置信度分支。

## Out of Scope

* 本阶段不做生产库写操作。
* 本阶段不做跨数据库联邦查询。
* 本阶段不追求完整 BI 语义层平台化。
* 本阶段不实现复杂权限系统或审计后台，只保留可扩展边界。

## Implementation Plan (small PRs)

* PR1: 引入 `SemanticQuery` 模型和汇总节点，补充单元测试，不改变外部 API。
* PR2: 改造 SQL 生成链路，让 `SemanticQuery` 成为主输入，`sql_plan` 仅作为兼容/调试层。
* PR3: 加入阈值执行门控、执行错误修复上下文和集成测试。
* PR4: 清理前端/后端响应说明与 debug 字段，补齐配置说明和回归测试。

## Technical Notes

* `backend/app/agent/graph.py` 当前链路已包含 query_understanding → retrieve_schema → schema_linking → value_linking → join_path_planning → build_semantic_brief → sql_planning → generate_sql → validate_sql → execute_sql → finalize_response。
* `backend/app/agent/state.py` 已有 `query_understanding`、`schema_linking`、`value_links`、`join_path_plan`、`business_semantic_brief`、`sql_plan`、`rows` 等状态字段，但还没有独立的 `semantic_query` 字段。
* `backend/app/agent/nodes.py:823` 的 `sql_planning` 仍产出 `sql_plan`，`backend/app/agent/nodes.py:941` 的 `generate_sql` 仍通过 `SQLGenerator().generate(sql_plan)` 渲染 SQL。
* `backend/app/rag/sql_planner.py` 的 `SQLPlan` 更像 SQL AST/渲染计划，不是面向用户意图的语义查询模型。
* `backend/app/rag/sql_generator.py` 当前只覆盖 select/from/join/where/order/limit，暂未渲染 group_by/having/metric expression，容易导致聚合类问题错配。
* `backend/app/database/executor.py` 已执行真实 SQL，并有只读校验、行数限制、超时入口和结果序列化。
* `backend/app/config.py` 已通过 `database_url`、`query_result_limit`、`database_readonly_required` 等配置支持数据库连接与安全边界。
* `backend/app/validator/sql_validator.py` 已有只读、多语句、危险关键词、plan provenance、SQL 与 plan 一致性校验，但生产化还需要更强 schema/权限/方言边界。
