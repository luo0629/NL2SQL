# update and wire field examples

## Goal

更新 `backend/config/field_examples.yaml`，使其更符合当前实际使用的数据表范围（当前 `.env` 指向 `jzjc` 及其纳入表），并以低风险方式接入 SQL 生成链路，优先用于字段命中和语义消歧，而不是做全量 few-shot 注入。

## What I already know

* 当前 `field_examples.yaml` 还是旧的 `jc_experimental` 委托类示例，不符合当前 `jzjc` 表范围。
* 当前 `.env` 的表范围是：
  * `jzjc.jiance_price`
  * `jzjc.hetong_price`
  * `jzjc.hetong`
  * `jzjc.gongcheng_price`
  * `jzjc.hetong_account`
  * `jzjc.weituo`
  * `jzjc.acceptance_slip`
* 当前主运行链路没有明显直接消费 `field_examples.yaml`；它目前只是被 `config_loader.py` 读取。
* 因此这轮任务既包括更新示例内容，也包括选择一个最小风险的接入点。

## Assumptions (temporary)

* 本轮目标是提升字段命中和语义消歧，不是重做完整 few-shot 体系。
* 注入方式应尽量按需，而不是把整份 `field_examples.yaml` 每次都塞进 prompt。
* 示例应围绕当前数据库实际表和字段，避免继续使用旧库示例。

## Open Questions

* MVP 更希望把 `field_examples.yaml` 接到哪一层？

## Requirements (evolving)

* 用当前实际表范围重写 `field_examples.yaml` 的内容。
* 接入方式应优先服务于字段命中/语义消歧。
* 不扩大成 `few_shot_samples.yaml` 全量注入。
* 尽量保持现有主链路稳定。

## Acceptance Criteria (evolving)

* [ ] `field_examples.yaml` 不再使用旧库示例。
* [ ] 至少有一条低风险接入链路，让 Agent 在字段命中/语义消歧时能参考这些示例。
* [ ] 不把整份示例无差别塞进 SQL 生成 prompt。
* [ ] 有相应测试或最小验证覆盖。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 重做完整 few-shot 体系
* 接入 `few_shot_samples.yaml`
* 大规模重写主 NL2SQL prompt 架构

## Technical Notes

* 现有文件：`backend/config/field_examples.yaml`
* 当前可读但未明显被主链路消费：`backend/app/config_loader.py`
* 当前数据库范围来自 `backend/.env`
