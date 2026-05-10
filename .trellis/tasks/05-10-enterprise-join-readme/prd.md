# brainstorm: enterprise join reliability and README refresh

## Goal

将 SQLAgent 明确收敛为企业级 SQL Agent 项目，而不是教学骨架：一方面分阶段提升联表键选择与联表可靠性，避免因脏字段、废弃字段或低覆盖率字段导致查不出数据；另一方面重写 README，使其准确表达当前能力、生产化方向与可信度，并采用更现代、专业、好看的开源项目呈现方式。

## What I already know

* 用户明确要求项目定位从“教学级”转为“企业级”。
* 用户接受此前提出的三阶段路线，并希望最终落点是企业级方案而非教学方案。
* 现有 README 仍以“NL2SQL 原型/教学骨架/mock 为主”叙事为核心。
* 后端并非纯教学骨架：`backend/app/config_generation.py` 已能生成 `table_relations`、`routing_suggestions`、`table_profiles`、`multi_hop_paths`。
* `backend/app/agent/nodes.py` 已存在 `_expand_selected_tables_with_relations`、`_selected_join_relations`、`_build_table_relations_overview`，说明项目已经有 join guidance 和 relation 注入能力。
* 现有 Trellis backend spec 要求文档应描述“项目的实际约定而非理想状态”，并使用英文文档风格作为规范基准。

## Assumptions (temporary)

* README 需要面向外部开发者/潜在协作者/评估者，而不仅是仓库作者本人。
* README 改写与 join reliability 三阶段方案可以放在同一总任务下推进，但实施时应拆成小阶段或子任务。
* 第一阶段应优先止血：基于已有 relation/override 机制提供推荐 join map 或 ranking 降权，而不是立刻引入完整在线统计系统。

## Open Questions

* 暂无。

## Requirements (evolving)

* 将项目叙事从“教学骨架”调整为“企业级 SQL Agent 的在建系统”。
* 为 join key 选择设计三阶段路线：止血、半自动增强、企业级关系/质量驱动。
* 本轮代码实现只落地第一阶段（止血），第二、三阶段写入 roadmap，作为后续企业化演进计划。
* README 需要准确反映当前已经存在的 relation/schema/join guidance 能力，而不是继续弱化为 mock demo。
* README 首屏同时服务企业技术评估与外部开发者：先建立可信度，再提供最短上手路径。
* 文档范围包括根 README、学习导向文档与相关入口说明的全套同步改写，而不是只改首页。
* README 风格需要现代、专业、可信，避免教程味和玩具感。
* 后续实现应优先服务企业级能力：真实执行、schema retrieval、cross-table reasoning、安全边界、观测与回归保护。

## Acceptance Criteria (evolving)

* [ ] 形成经过确认的三阶段企业化路线，并明确每阶段目标与边界。
* [ ] 本轮只实现第一阶段止血方案，且方案能降低选择废弃/高空值 join key 的概率。
* [ ] README 首要叙事同时兼顾企业技术评估者与外部开发者。
* [ ] 新 README 不再使用“教学骨架/原型项目”作为主定位。
* [ ] 新 README 能准确描述当前关键能力、架构方向、快速开始、系统特性与 roadmap。
* [ ] 学习导向文档及相关入口说明完成同步改写，不再主导项目首页叙事。
* [ ] join reliability 改造方案与 README 叙事保持一致，不互相矛盾。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 本轮不一次性完成所有企业级能力（如完整执行沙箱、权限系统、全量观测平台）。
* 本轮不把 README 写成学习教程或入门课程。
* 本轮不以多智能体扩展为优先目标。

## Research References

* `research/readme-style.md` — 待补充：现代、好看的 README 结构与视觉模式。
* `research/enterprise-positioning.md` — 待补充：企业级 AI/数据项目 README 的可信叙事方式。

## Technical Notes

* README current state: `README.md`
* Join/relation generation: `backend/app/config_generation.py:_build_relations_payload`
* Join relation selection/overview: `backend/app/agent/nodes.py:_expand_selected_tables_with_relations`
* Join relation rendering: `backend/app/agent/nodes.py:_selected_join_relations`
* Relation context injection: `backend/app/agent/nodes.py:_build_table_relations_overview`
* Backend guideline index: `.trellis/spec/backend/index.md`
