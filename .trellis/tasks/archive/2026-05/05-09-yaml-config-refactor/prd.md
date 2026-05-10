# 将 YAML 配置按职责拆分为独立文件

## Goal

将所有 YAML 配置按职责拆分为 6 个独立文件，统一放在 `config/` 目录下。代码侧在启动时统一加载，各节点按需读取。同时为 `field_semantics.yaml` 中的跨表同名字段补充区别说明。

## What I already know

### 现有配置结构
- `yaml/business_semantics_mysql_asyncmy_*.yaml` — 自动生成的业务语义，包含 `generated`（自动生成）和 `overrides`（手动编辑）两部分
- `backend/app/rag/schema_enrichment.py` — 硬编码的 Pydantic 模型，覆盖 11 张表（外卖系统）
- `backend/app/rag/value_mappings.json` — JSON 格式的枚举值映射（dish/setmeal 的 status）
- `backend/app/prompts/few_shot_examples.json` — 6 个示例 Q&A（当前未被加载）
- `backend/app/prompts/nl2sql_prompt.txt` — 系统提示词（当前未被加载）

### 现有加载链路
```
config.py → schema_sync.py
  → schema_enrichment.py (硬编码)
  → value_mapping_loader.py (JSON)
  → business_semantics.py (YAML 读写合并)
    → rag_service.py (缓存)
```

### 跨表同名字段（jc_experimental 库）
- `revision`, `creator`, `updater`, `reserve1`-`reserve6` — 主要在 `weituo_settle_bill`
- `status` — 出现在 `weituo`（委托状态）和 `weituo_settle_bill`（清算状态）
- `remark` — 出现在多张表

### 关键发现
- 现有 YAML 是**自动生成 + 手动覆盖**的双层结构
- `schema_enrichment.py` 覆盖的是**外卖系统**（orders/dish/setmeal），不是 jc_experimental
- few_shot_examples.json 和 prompt 文件当前**未被使用**

## Assumptions

- 新的 `config/` 目录替代现有的 `yaml/` 目录
- 自动生成功能保留，但生成内容写入对应职责文件
- `schema_enrichment.py` 的硬编码内容迁移到 YAML 文件中

## Decision (ADR-lite)

**Context**: 现有 YAML 是单文件自动生成+手动覆盖的双层结构，需要拆分为多文件按职责管理
**Decision**: 保留生成+手动双层结构。自动生成的内容作为默认值写入各文件的 `generated` 部分，手动编辑的内容写入 `overrides` 部分。启动时加载合并，手动优先。
**Consequences**: 每个 YAML 文件都有 `generated` 和 `overrides` 两个 section；自动同步时只更新 `generated`，保留 `overrides`

## Requirements

### 文件结构
```
backend/config/
├── table_relations.yaml      # 表关系、关联字段、路由建议、字段等价
├── field_semantics.yaml      # 字段业务含义、同名字段区分、负向说明
├── field_examples.yaml       # 字段查询示例
├── enum_mappings.yaml        # 枚举值口语映射
├── business_terms.yaml       # 用户口语表达标准化
└── few_shot_samples.yaml     # Few-shot 样本
```

### 各文件职责

**table_relations.yaml**
- 表间外键关系
- 关联字段定义
- 路由建议（用户问 X 时优先查哪些表）
- 字段等价关系（不同表的字段表示同一业务概念）

**field_semantics.yaml**
- 每个字段的业务含义描述
- 取值范围或枚举说明
- 跨表同名字段的区别说明（重点！）
- 负向说明（这个字段不是什么）

**field_examples.yaml**
- 字段的典型查询示例
- 包含自然语言问题和对应 SQL

**enum_mappings.yaml**
- 枚举值到口语表达的映射
- 从现有 value_mappings.json 迁移

**business_terms.yaml**
- 用户口语表达到标准术语的映射
- 别名、缩写、同义词

**few_shot_samples.yaml**
- Few-shot 示例样本
- 从现有 few_shot_examples.json 迁移并适配 jc_experimental 库

### 代码侧改动
- 新建 `backend/app/config_loader.py` — 统一加载所有 YAML 文件
- 修改 `backend/app/rag/business_semantics.py` — 从新文件读取配置
- 修改 `backend/app/rag/schema_enrichment.py` — 硬编码内容迁移到 YAML
- 修改 `backend/app/rag/schema_sync.py` — 适配新加载链路
- 修改 `backend/app/config.py` — 新增 config_dir 配置项

### field_semantics.yaml 跨表同名字段处理
为每个跨表同名字段在每张表中分别写清楚业务区别：
- `status`：weituo 表是委托状态（待审核/已通过/已驳回），weituo_settle_bill 表是清算状态
- `remark`：各表的备注字段含义不同
- `creator`/`updater`：通用审计字段，各表含义一致
- `reserve1`-`reserve6`：预留字段，各表含义可能不同

## Acceptance Criteria

- [ ] 6 个 YAML 文件创建在 `backend/config/` 目录下
- [ ] 每个文件包含对应职责的配置内容
- [ ] field_semantics.yaml 包含跨表同名字段的区别说明
- [ ] 现有 schema_enrichment.py 硬编码内容迁移到 YAML
- [ ] 现有 value_mappings.json 内容迁移到 enum_mappings.yaml
- [ ] 现有 few_shot_examples.json 内容迁移到 few_shot_samples.yaml
- [ ] config_loader.py 统一加载所有 YAML 文件
- [ ] 各节点按需读取对应部分
- [ ] 现有测试全部通过
- [ ] 新增 config_loader 的单元测试

## Out of Scope

- 修改前端
- 修改 agent graph 流程
- 新增 few-shot 样本（只迁移现有内容）
- 自动生成逻辑的大改（保持兼容）

## Technical Notes

- 现有 YAML 路径: `yaml/business_semantics_mysql_asyncmy_cd3ae5cfe69e62c7.yaml`
- 硬编码 enrichment: `backend/app/rag/schema_enrichment.py`
- value_mappings: `backend/app/rag/value_mappings.json`
- few_shot: `backend/app/prompts/few_shot_examples.json`
