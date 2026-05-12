# clean highest-risk old-db residue

## Goal

清理项目中与旧数据库（尤其是 `jc_experimental` / `jc_config`）相关的最高风险残留，只优先处理会锚定 live prompt、误导初始化配置、或明显干扰当前数据库切换的内容，避免为了清尾巴把整套示例资产一次性重构掉。

## What I already know

* `backend/app/agent/nodes.py:1088` 的 live prompt 仍然使用 `` `jc_config`.`table` `` / `` `jc_experimental`.`table` `` 作为全限定表示例，这会在运行时对模型形成旧库锚点。
* `backend/.env.example:7-8` 仍默认指向 `jc_experimental` 和对应表范围，容易让新环境初始化时重新回到旧库。
* `backend/config/field_examples.yaml` 与 `backend/config/few_shot_samples.yaml` 仍然包含大量 `jc_experimental` 示例 SQL，但目前更偏示例/辅助配置，不一定是当前主运行链路依赖。
* 当前主运行链路已经支持按新数据库 scope 刷新核心 YAML 和 business semantics，不再需要依赖旧库作为运行时默认知识。

## Assumptions (temporary)

* 本轮只治理“最高风险残留”，不做全面示例体系重构。
* 优先级最高的是 live prompt 和初始化模板，其次才是示例 YAML。
* 目标是减少旧库心智回流，而不是立即删除所有历史痕迹。

## Open Questions

* MVP 清理范围是否只做最高风险的 live prompt + `.env.example`，还是顺手把示例 YAML 也一起泛化？

## Requirements (evolving)

* 清理会直接影响当前运行或初始化体验的旧数据库残留。
* 优先处理：
  * live prompt 中的旧库示例
  * `.env.example` 中的旧库默认配置
* 本轮不处理 `few_shot_samples.yaml`。
* 如范围允许，可顺手处理 `field_examples.yaml`，但不扩大成整套示例体系重构。
* 尽量保持项目示例能力，但不要再把旧库名字作为默认知识来源。

## Acceptance Criteria (evolving)

* [ ] live prompt 不再锚定 `jc_config` / `jc_experimental` 作为全限定表示例。
* [ ] `.env.example` 不再默认指向旧数据库。
* [ ] 如果纳入本轮，示例 YAML 也不再把旧库名字作为默认样例核心。
* [ ] 不引入新的运行时回归。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 一次性清理所有测试、归档任务、历史文档中的旧库名字
* 完整重做 few-shot / field examples 体系
* 改动数据库运行时 schema 刷新主链路

## Technical Notes

* 最高风险文件：
  * `backend/app/agent/nodes.py`
  * `backend/.env.example`
* 次级残留：
  * `backend/config/field_examples.yaml`
  * `backend/config/few_shot_samples.yaml`
* 当前判断：old fingerprint 生成 YAML / artifacts 和测试文档残留属于低风险，可后续单独处理。
