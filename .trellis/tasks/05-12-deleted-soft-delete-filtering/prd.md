# deleted soft-delete filtering

## Goal

为当前项目设计并实现一版 deleted 软删除过滤策略：当表中存在 `deleted` 字段时，查询默认应确保 `deleted = 0`，因为用户查询的数据默认应是未删除的数据；但当用户明确要求查询已删除数据时，应切换为 `deleted = 1`。同时检查并补齐 YAML 中对 `deleted` 枚举语义（`0=未删除, 1=删除`）的表达，并明确这类约束应落到哪一层配置/逻辑。

## Requirements

* 当表存在 `deleted` 字段时，默认查询应确保 `deleted = 0`。
* 当用户明确要求查询已删除数据、删除记录时，应切换为 `deleted = 1`。
* 当用户明确要求查询全部数据/包含删除数据时，应允许不自动补默认 `deleted = 0`。
* MVP 先落在**生成层**：优先在 SQL 生成 / fallback 层补 `deleted` 条件，不先扩展到校验层强制重试。
* YAML 中应能够表达 `deleted` 的枚举语义（至少 `0=未删除, 1=删除`）。
* 需要明确职责分层：
  * `field_semantics.yaml` -> 字段语义
  * `enum_mappings.yaml` -> `deleted` 的值语义
  * SQL 生成逻辑 / 策略层 -> 默认过滤与显式覆盖行为
* 尽量保持现有 SQL 生成链路稳定。

## Acceptance Criteria

* [ ] 默认查询能对存在 `deleted` 字段的表补上 `deleted = 0`。
* [ ] 用户明确要求已删除数据时，会生成 `deleted = 1`。
* [ ] 用户明确要求全部/包含删除数据时，不会被强行加上 `deleted = 0`。
* [ ] YAML 中能表达 `deleted` 的枚举语义。
* [ ] 相关测试覆盖默认过滤、已删除查询、全部数据查询三类场景。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 一次性扩展到所有软删字段别名（如 `is_deleted`, `delete_flag` 等）
* 前端展示逻辑调整
* 对所有状态字段做统一默认过滤
* 本轮在校验层强制补 deleted 重试

## Technical Approach

MVP 先治理生成层：在 SQL 生成 / fallback 逻辑中，当表存在 `deleted` 字段时，根据用户意图自动决定补 `deleted = 0`、`deleted = 1` 或不补；同时在 YAML 中补充 `deleted` 的枚举语义，保证模型能把“未删除 / 已删除 / 全部”这类自然语言映射到正确值语义。校验层暂不扩展，以降低改动面。

## Decision (ADR-lite)

**Context**: 当前系统虽然把 `deleted` 视为 internal 字段，但没有把它当作默认业务过滤条件，也没有明确记录 `0=未删除, 1=删除` 的值语义，因此查询默认结果可能混入已删除数据。

**Decision**: 本轮采用“生成层优先”的 MVP 路线：默认查询补 `deleted = 0`，明确查删除时切换成 `deleted = 1`，明确查全部时不自动加默认过滤；同时把 `deleted` 的枚举语义写入 YAML。

**Consequences**: 这样能快速改善查询结果正确性，并保持改动范围最小；代价是校验层暂时不兜底，后续如果模型仍偶发漏加条件，可能需要再扩展到 validator。

## Technical Notes

* 已确认：`field_semantics.yaml` 已有 `deleted` 的 internal 语义，但 `enum_mappings.yaml` 尚未记录它的值语义。
* 候选落点：
  * `field_semantics.yaml` -> 字段语义说明
  * `enum_mappings.yaml` -> `0/1` 枚举语义
  * `backend/app/agent/nodes.py` 等生成逻辑 -> 默认补过滤策略
