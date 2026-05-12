# govern count field selection

## Goal

治理 SQL 生成中的 COUNT 口径选择，避免模型默认依赖技术主键 `id` 来做统计，而是优先使用更贴近用户问题语义的业务字段进行 COUNT 或 DISTINCT COUNT，减少统计口径被技术键误导的问题。

## What I already know

* 当前项目没有明显现成的 COUNT 口径配置层，`backend/config/*.yaml` 中也没有专门定义 count_field / distinct_count_field 之类规则。
* 目前更像是模型根据 prompt、schema_context 和字段语义自己猜 COUNT 口径，因此容易回到最保险但业务含义很弱的 `id`。
* 现有系统已经具备字段语义、枚举语义、field_examples、表级策略等多种配置层，因此 COUNT 规则可以沿用现有分层思路，不必新造太重的体系。

## Assumptions (temporary)

* 用户说“数量/条数/多少个”时，不一定都应该统计 `id`。
* 有些场景应统计业务主键/业务编号；有些场景应统计 DISTINCT 业务字段；也有些场景确实可以统计行数。
* 本轮应优先减少明显错误的技术主键依赖，而不是一次性做完整聚合语义引擎。

## Open Questions

* MVP 更希望先治理哪一类 COUNT 场景？

## Requirements (evolving)

* 避免 COUNT 默认依赖技术主键 `id`。
* 优先选择更贴近用户问题语义的业务字段进行 COUNT。
* 需要明确哪些信息属于：
  * 字段语义
  * 表级策略/override
  * prompt 生成约束
* 尽量保持现有主链路稳定，不扩大成完整聚合重构。

## Acceptance Criteria (evolving)

* [ ] 常见 COUNT 场景不再盲目落到 `COUNT(id)`。
* [ ] COUNT 字段选择能体现业务字段语义。
* [ ] 配置层职责清晰，不把所有聚合逻辑混在一层。
* [ ] 有对应测试覆盖主要 COUNT 口径选择场景。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 完整重写所有聚合函数语义
* 前端展示改造
* 一次性覆盖所有统计口径特例

## Technical Notes

* 可能影响：`backend/app/agent/nodes.py`
* 现有可复用分层：`field_semantics.yaml`、`agent_strategy.yaml`、`field_examples.yaml`
* 当前缺口：没有明确的 COUNT 字段/去重字段选择规则。
