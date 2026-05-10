# config yaml auto generation

## Goal

为当前项目增加 `backend/config/` 下拆分 YAML 的自动生成能力，使数据库 schema 发生变更时，配置中的可推导部分能够自动刷新或重建，尽量保持结构描述与真实数据库一致，同时保留人工补充的业务语义覆盖层，避免手工维护与数据库实际结构漂移。

## What I already know

* 当前项目已经有通过读取数据库结构生成 `yaml/` 目录下业务语义 YAML 的能力。
* `backend/app/rag/business_semantics.py` 已实现按数据库指纹生成和刷新 YAML 文件的机制，并保留 `overrides`。
* `backend/app/config_loader.py` 当前只负责静态读取 `backend/config/` 下 6 个 YAML：`table_relations.yaml`、`field_semantics.yaml`、`field_examples.yaml`、`enum_mappings.yaml`、`business_terms.yaml`、`few_shot_samples.yaml`。
* `backend/app/schema_watcher.py` 当前会轮询 `INFORMATION_SCHEMA`，检测 schema 变更后触发 `sync_schema_metadata()` 和 `invalidate_schema_cache()`，但不会刷新 `backend/config/`。
* `backend/app/rag/schema_sync.py` 能从数据库获取表、列、类型、注释、主键、外键等结构信息。
* `backend/config/*.yaml` 采用 `generated` + `overrides` 双层结构，运行时通过 `_merge_generated_overrides()` 合并。

## Assumptions (temporary)

* 适合自动生成的是结构性配置；强业务语义、NL 示例、few-shot 不适合全自动覆盖。
* 更稳妥的方案是：自动刷新 `generated`，保留人工维护的 `overrides` 不动。
* 自动化应覆盖初始化与后续 schema 变更两个阶段，不依赖人工执行重建命令。

## Open Questions

* 无。

## Requirements (evolving)

* 为 `backend/config/` 下 YAML 增加自动生成/自动刷新能力。
* MVP 自动刷新这 4 类配置：`table_relations.yaml`、`field_semantics.yaml`、`enum_mappings.yaml`、`business_terms.yaml`。
* `business_terms.yaml` 在 MVP 中只自动生成基础别名/骨架，供人工在 `overrides` 中继续补充真正业务术语。
* `field_examples.yaml` 与 `few_shot_samples.yaml` 保持人工维护，不纳入自动内容生成范围。
* 初始化阶段应自动完成上述 4 个 YAML 的全量生成/刷新，不依赖人工执行命令。
* 数据库 schema 变更后，schema watcher 检测到变更时应自动刷新可推导配置。
* 生成逻辑必须保留人工覆盖层，不能覆盖 `overrides`。
* 生成结果应与现有 `config_loader.py` 的读取/合并模型兼容。

## Acceptance Criteria (evolving)

* [ ] 初始化阶段会自动生成或刷新 `table_relations.yaml`、`field_semantics.yaml`、`enum_mappings.yaml`、`business_terms.yaml` 的 `generated` 内容。
* [ ] schema 变更后，上述 4 个 YAML 的 `generated` 内容会自动刷新。
* [ ] `overrides` 内容在初始化刷新和后续刷新后仍保留。
* [ ] `field_examples.yaml` 与 `few_shot_samples.yaml` 不会被自动刷新逻辑覆盖。
* [ ] 生成后的 YAML 结构继续兼容当前配置加载逻辑。
* [ ] 自动生成范围与非自动生成范围有清晰边界。

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 让 LLM 自动生成高质量业务术语、few-shot、查询样例并直接覆盖人工内容。
* 引入外部配置中心或复杂的多版本迁移系统。

## Technical Notes

* `backend/app/config_loader.py`：当前 config YAML 静态读取入口。
* `backend/app/schema_watcher.py`：当前 schema 变更检测入口，可作为自动刷新触发点。
* `backend/app/rag/schema_sync.py`：当前数据库结构采集入口，可复用为 config 生成的数据源。
* `backend/app/rag/business_semantics.py:519-542`：现有 YAML 自动刷新参考实现，核心模式是“生成 generated + 保留 overrides”。
* 很可能需要新增单独的 config generation 模块，而不是把逻辑塞进现有 loader/sync 文件里。
