# remove cangqiong hardcoding and auto-refresh yaml

## Goal

去掉项目中与“苍穹外卖”/固定示例数据库相关的后端硬编码，让系统更多依赖实时数据库 schema 自适应生成语义与治理配置；并在应用启动时根据当前数据库表结构自动刷新核心 YAML 数据，同时尽量保留人工 `overrides`。

## Requirements

* 移除或弱化后端运行时对固定示例数据库（如苍穹外卖 / `jc_experimental`）的硬编码依赖。
* 系统应尽量基于当前真实数据库 schema 自适应生成或刷新 YAML 数据。
* MVP 只做**启动时自适应刷新**：启动时统一跑一次 schema sync + 核心 YAML 刷新；本轮不主动重构运行中的 `schema_watcher.py` 行为。
* 启动时应自动执行核心 YAML 刷新链路。
* 自动刷新的核心 YAML 范围为：
  * `backend/config/table_relations.yaml`
  * `backend/config/field_semantics.yaml`
  * `backend/config/enum_mappings.yaml`
  * `backend/config/business_terms.yaml`
  * `yaml/business_semantics_<scope>.yaml`
* 人工 `overrides` 需要尽量保留。
* 数据库暂时不可连接、schema 缺少注释/枚举线索等情况需要安全降级，不能退回到苍穹外卖特定知识。
* schema 未变化时应尽量避免无意义重写 YAML。
* 行为需要有测试覆盖，尤其是启动刷新、schema scope 隔离、以及缺少注释/枚举时的降级行为。

## Acceptance Criteria

* [ ] 运行时关键路径不再依赖苍穹外卖特定表名/字段名作为默认知识来源。
* [ ] 启动时能够根据当前数据库 schema 刷新核心 YAML。
* [ ] YAML 刷新不会破坏人工 `overrides`。
* [ ] 不同数据库 scope 的 YAML 仍能隔离。
* [ ] 数据库不可连或 schema 信息不足时，系统仍能安全启动并给出可控降级行为。
* [ ] 相关后端测试通过。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Technical Approach

以现有 `sync_schema_metadata()` 和 `refresh_generated_config_yaml()` 为基础，补一条统一的启动期自适应刷新链路：应用启动时先做一次 schema 驱动的同步/刷新，确保 `backend/config/*.yaml` 的 generated 区与按 scope 隔离的 `yaml/business_semantics_<scope>.yaml` 都能基于当前数据库结构更新，同时保留 `overrides`。本轮不重构运行期 watcher，只解决启动路径与硬编码默认知识问题。

## Decision (ADR-lite)

**Context**: 目前启动刷新和 business semantic YAML 刷新分裂成两条链路，且部分默认知识仍带有苍穹外卖 / `jc_experimental` 的样例痕迹，导致系统对数据库变化的自适应能力不足。

**Decision**: 本轮采用“只做启动自适应刷新”的 MVP 路线，统一启动期核心 YAML 刷新，不顺手扩大到 watcher 重构。

**Consequences**: 这样能以较低风险先补齐启动时的 schema 自适应能力，并减少固定示例数据库知识对运行时的影响；代价是运行中 schema 变化的刷新一致性问题留待后续单独治理。

## Out of Scope (explicit)

* 前端页面改造
* 完整重做 few-shot 体系
* 一次性清空所有示例测试数据
* 自动刷新 `field_examples.yaml`、`few_shot_samples.yaml` 这类示例型 YAML
* 本轮重构 `schema_watcher.py` 的运行期刷新架构

## Technical Notes

* 重点文件：
  * `backend/app/rag/schema_sync.py`
  * `backend/app/config_generation.py`
  * `backend/app/main.py`
  * `backend/app/schema_watcher.py`
  * `backend/app/rag/business_semantics.py`
  * `backend/app/rag/value_mapping_loader.py`
  * `backend/app/rag/value_mappings.json`
  * `backend/app/agent/nodes.py`
* 已知运行时硬编码：
  * `schema_sync.py` 中的 `TABLE_DESCRIPTIONS`
  * `value_mappings.json` 中 `dish.status` / `setmeal.status`
  * `nodes.py` 中 `jc_config` / `jc_experimental` 示例文本
* 已知关键差距：启动刷新和 business semantic YAML 刷新目前是分裂的两条链路。
* 已知约束：`backend/tests/unit/test_main.py`、`test_schema_watcher.py`、`test_config_generation.py`、`test_business_semantics.py`、`test_rag_service_cache.py`、`test_config_loader.py`、`test_config.py` 需要重点关注。
