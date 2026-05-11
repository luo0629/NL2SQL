# govern order-by selection

## Goal

为当前项目设计并落地 ORDER BY 选择策略，避免 fallback 或自动生成 SQL 时因为按主键/第一列排序导致结果语义错乱。希望结合字段语义、用户排序意图、表级 override 来决定排序字段，并明确哪些信息应写入 `field_semantics.yaml`、`agent_strategy.yaml` 等配置层。

## What I already know

* 当前 SQL 生成 prompt 要求“如需要 LIMIT，必须同时给出稳定 ORDER BY”。
* 当前 fallback SQL 在 `backend/app/agent/nodes.py` 中直接使用：优先主键，否则第一列，并统一 `DESC`。
* 这会导致技术键排序替代业务键排序，容易让结果看起来“乱”。
* `field_semantics.yaml` 现阶段适合承载字段语义、业务术语、跨表差异等信息。
* `agent_strategy.yaml` 现阶段适合承载策略型约束和偏好，如禁用字段、fallback 参数等。

## Assumptions (temporary)

* 本轮重点先治理后端 ORDER BY 选择逻辑，不涉及前端展示。
* “数据错乱”主要指排序字段语义不对，而不是数据库本身返回不稳定。
* 表级 override 是合理入口，但不应把所有字段语义都塞进策略层。

## Open Questions

* MVP 的作用范围应该先落在哪一层？

## Requirements (evolving)

* 避免 fallback 或自动生成 SQL 时按主键/第一列盲目排序。
* 排序策略需要综合：
  * 用户显式排序意图
  * 字段语义
  * 表级 override
* 需要区分哪些信息属于：
  * 字段语义配置
  * 策略配置
  * prompt 行为约束
* 尽量保持现有 SQL 生成链路稳定。

## Acceptance Criteria (evolving)

* [ ] fallback SQL 不再简单使用主键/第一列作为默认 ORDER BY。
* [ ] ORDER BY 字段选择可体现用户意图与字段语义。
* [ ] 配置层职责分工清晰，不把语义信息和策略信息乱混。
* [ ] 相关单测可覆盖主要排序选择场景。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 前端排序交互
* 一次性重做整个 NL2SQL prompt 体系
* 不区分字段语义就做大范围模糊匹配

## Technical Notes

* 关键代码：`backend/app/agent/nodes.py:1081-1137`
* 当前风险点：prompt 只要求“稳定 ORDER BY”，fallback 实现却默认用技术键排序。
* 候选配置层：
  * `backend/config/field_semantics.yaml`
  * `backend/config/agent_strategy.yaml`
