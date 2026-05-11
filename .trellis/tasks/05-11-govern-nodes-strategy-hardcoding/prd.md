# govern nodes.py strategy hardcoding

## Goal

将 `backend/app/agent/nodes.py` 中直接影响运行时行为的策略型硬编码抽离出来，并优先支持**自主配置某张表中的某些键为“不要使用”**，使 Agent 在生成 SQL 时尽量忽略这些不应参与选择、展示或 JOIN 的键，同时保持当前行为兼容，降低后续调参、治理和跨模块复用的成本。

## Requirements

* 抽离 `nodes.py` 中最关键的策略硬编码，至少包括：
  * 词表/term sets
  * JOIN 偏好打分参数
  * fallback 候选数量与 SQL 默认参数
* 新增可配置能力：允许按表配置“不要使用”的键，供 Agent 在生成 SQL 时避开。
* 表级禁用键采用**全链路禁用**：在 `nodes.py` 涉及的表筛选、展示列选择、JOIN 候选、fallback SQL 等相关选择逻辑中都应尽量避开这些键。
* 本轮采用最小治理路线：尽量保持 `nodes.py` 的调用结构稳定，不主动扩大到跨模块共享策略层。
* 本轮只治理真正驱动逻辑分支的内容，不处理 `_build_sql_generation_prompt()` 中固定策略文案的全面外置。
* 尽量保持现有 agent 行为兼容。
* 优先沿用仓库现有配置模式，避免引入全新配置框架。
* 配置缺失时必须安全回退到默认策略。
* 保持现有测试可延续，必要时补充或调整测试以锁定新策略层行为。

## Acceptance Criteria

* [ ] `nodes.py` 中核心策略常量不再直接散落为模块级硬编码。
* [ ] 词表、JOIN 权重、fallback 参数可通过统一策略来源读取。
* [ ] 可以按表配置不应使用的键。
* [ ] 表级禁用键会在 `nodes.py` 相关 SQL 生成选择逻辑中全链路生效。
* [ ] 默认策略缺失或未配置时，运行时仍保持安全且与当前主干兼容的默认行为。
* [ ] 最小治理范围内不引入跨模块大规模重构。
* [ ] 现有关键单测继续通过，尤其是 `backend/tests/unit/test_agent_graph_schema_plan.py`。
* [ ] 改造后默认行为与当前主干兼容，没有明显回归。

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Technical Approach

在 `nodes.py` 外引入一个统一的策略来源，优先复用现有 `backend/config/` + `backend/app/config_loader.py` 模式承载默认策略；`nodes.py` 保留流程骨架，只改为消费集中化的策略对象或策略读取函数。首轮覆盖词表、JOIN 打分参数、fallback 候选数量与 fallback SQL 参数，并新增“表级禁用键”配置，让 Agent 在 `nodes.py` 相关选择逻辑中全链路避开这些键；不扩大到跨模块共享层，也不强行改写 prompt 文案体系。

## Decision (ADR-lite)

**Context**: `nodes.py` 当前把字段词表、JOIN 分数、fallback 参数直接写死在模块常量和函数内部，导致调参困难、测试意图分散、且无法对具体表声明“这些键虽然存在，但不该参与 SQL 生成”。

**Decision**: 本轮采用“最小治理 + 纯逻辑参数治理”路线，只抽离 `nodes.py` 内真正驱动逻辑分支的策略参数；同时加入表级禁用键配置，并按全链路禁用处理。暂不主动把 `business_semantics.py` 等相邻模块一起收敛，也不全面外置 prompt 文案。

**Consequences**: 这能以较低风险完成第一优先级治理，并直接解决“某些表键不该被 Agent 使用”的需求；代价是短期内相邻模块重复策略仍会存在，需要后续单独治理。

## Out of Scope

* 全面治理 `backend/app/rag/business_semantics.py`
* 全面治理 `backend/app/validator/sql_validator.py`
* 重做 schema governance artifact 生成流程
* 大规模重写 prompt 体系或 agent graph 结构
* 将跨模块重复策略全面收敛成共享策略层
* 本轮全面外置 `_build_sql_generation_prompt()` 中的固定策略文案

## Technical Notes

* 目标文件：`backend/app/agent/nodes.py`
* 可复用配置模式：`backend/app/config_loader.py`、`backend/app/rag/schema_enrichment.py`
* 现有配置目录：`backend/config/`
* 关键测试：`backend/tests/unit/test_agent_graph_schema_plan.py`
* 相邻模块：`backend/app/rag/business_semantics.py`、`backend/app/rag/schema_sync.py`、`backend/app/rag/schema_governance.py`
* 已确认：本轮不扩大为跨模块共享策略层重构。
