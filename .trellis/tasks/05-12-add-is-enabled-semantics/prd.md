# add is_enable semantics

## Goal

检查当前 `.env` 纳入范围内哪些表存在 `is_enable` 字段，并为这些表补充启用状态枚举语义：`0=不启用`、`1=启用`，保证后续 SQL 生成能正确理解；同时要求该语义在 schema/YAML 刷新时也能自动带上。

## Requirements

* 只关注当前 `.env` 范围内的表，不扩展到整个数据库的所有 `is_enable` 表。
* 当前范围与 `is_enable` 命中表的交集为：
  * `jzjc.jiance_price`
  * `jzjc.hetong_price`
  * `jzjc.gongcheng_price`
  * `jzjc.weituo`
* 为这些表的 `is_enable` 字段补充值语义：
  * `0 = 不启用`
  * `1 = 启用`
* 该语义应在 schema/YAML 刷新时自动生成，而不是只靠手工写死在 overrides。
* 尽量保持现有配置职责分层：
  * `enum_mappings.yaml` 负责值语义
  * 不额外扩展到默认过滤/启用态策略
* 不扩展到 `is_enabled`、`enable_flag` 等其它命名变种。

## Acceptance Criteria

* [ ] 当前 `.env` 范围内命中的 `is_enable` 字段在生成后的 `enum_mappings.yaml` 中具备 `0/1` 启用语义。
* [ ] 不在 `.env` 范围内的表不会因为这轮改动被顺手加入该规则。
* [ ] 刷新生成链路会自动带上这项语义。
* [ ] 相关测试覆盖生成行为。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 扩展到所有 `is_enable` 表
* 扩展到 `is_enabled` / `enable_flag` / `status` 等其它命名变种
* 给 `is_enable` 增加默认过滤或查询偏好逻辑
* 手工在 overrides 中长期写死而不改自动生成逻辑

## Technical Notes

* 当前仓库 YAML 里还没有 `is_enable` 记录。
* 已确认当前数据库 `jzjc` 中存在 `is_enable` 的表有：
  * `conclusion_template`
  * `gongcheng_price`
  * `hetong_price`
  * `jiance_price`
  * `online_sign`
  * `parameter`
  * `sample`
  * `weituo`
* 但本轮只处理 `.env` 当前范围和上面命中的交集 4 张表。
* 主要落点预计在 `backend/app/config_generation.py` 与相关测试。
