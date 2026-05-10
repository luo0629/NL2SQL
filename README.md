<div align="center">
  <h1>SQLAgent</h1>
  <p><strong>Enterprise-oriented SQL Agent for governed NL2SQL, join-aware schema reasoning, and controlled query execution.</strong></p>
  <p>面向中文业务查询场景，聚焦可审查 SQL、联表可靠性、只读安全边界与逐步生产化落地。</p>
</div>

---

## SQLAgent 是什么

SQLAgent 是一个正在持续演进的企业级 SQL Agent 项目。
它的目标不是把自然语言“翻译成一段看起来像 SQL 的文本”，而是建立一条可治理的查询链路：

- 从真实 schema 中选择相关表与字段
- 在跨表场景下优先使用更可靠的 join 关系
- 在执行前进行只读校验与受控验证
- 返回 SQL、结果、执行摘要与调试线索，便于审查与迭代

当前仓库已经不再只是一个首页演示壳。它包含实际的 LangGraph 查询流、schema catalog、relation 注入、join guidance、SQL 校验与执行路径，并且正在沿着企业化方向持续补强。

---

## 当前能力边界

### 已具备

- LangGraph 驱动的 NL2SQL 工作流
- 基于真实 schema catalog 的表选择与 schema context 渲染
- 关系感知的跨表上下文注入
- join hint / confidence / cross-table diff 等联表辅助信息
- 只读 SQL 校验与 MySQL EXPLAIN 预检路径
- SQL 执行结果返回：`rows`、`columns`、`row_count`、`execution_summary`
- 前端工作台可展示 SQL、参数、结果集与调试信息

### 当前仍在完善

- 更强的 schema retrieval 与业务语义覆盖
- 更稳健的 value grounding 与自然语言约束解析
- 更严格的权限、审计、可观测性与回归体系
- 更成熟的生产级安全策略与发布流程

### 诚实说明

- 默认 LLM 模式仍可运行在 `mock`/fallback 路径，便于本地开发与稳定测试。
- 项目定位已经转向企业级 SQL Agent，但当前仓库仍处于“在建系统”阶段，而不是已经完整交付的生产平台。
- README 会优先说明当前真实能力与明确边界，而不是用空泛的“enterprise-ready”口号掩盖现状。

---

## 为什么这个项目值得关注

| 维度 | 当前做法 | 价值 |
| --- | --- | --- |
| 架构边界 | `routers / services / agent / rag / validator / database / prompts` 分层 | 避免把生成、校验、执行混在一起 |
| 联表可靠性 | schema relation、join hint、cross-table diff、relation confidence | 降低脏字段、通用字段、废弃式字段被误选为 join key 的概率 |
| 安全边界 | 只读校验、受控 EXPLAIN、执行器二次校验 | 保持生成与执行边界清晰 |
| 结果可审查 | API 返回 SQL、参数、结果、摘要、错误与 debug | 便于人工 review、回归与诊断 |
| 演进方向 | 真实执行、schema grounding、cross-table reasoning、observability | 与企业 SQL Agent 的实际落地方向一致 |

---

## 联表可靠性路线

本仓库当前任务的核心之一，是把“能生成 join”推进到“更可靠地选择 join key”。

### Stage 1：止血，已实现

当前版本已经落地第一阶段联表可靠性增强，重点是避免模型优先使用低质量、低语义、易误连的字段：

- 基于 live schema 生成 `table_relations`、`routing_suggestions`、`table_profiles`、`multi_hop_paths`
- 在 schema context 中显式注入 `Relations`、`hint`、`confidence`
- 对跨表重名字段补充 `cross_table_diff`，提示哪些字段不要默认作为 JOIN 键
- 优先保留业务主编号、外键等更可靠的关联键
- 降低时间字段、状态字段、名称字段、审计字段被盲目同名联表的概率

### Stage 2：半自动增强，路线图

下一阶段将继续补：

- join key ranking 与更细粒度的 relation scoring
- 基于样本值/分布的半自动关系校验
- 对低覆盖率、高空值、高歧义字段做更强降权

### Stage 3：关系质量驱动，路线图

长期方向包括：

- 面向真实数据库统计与质量信号的 relation governance
- 更严格的 join path selection 与多跳联表控制
- 权限、审计、观测、评测共同参与联表质量闭环

---

## 一次请求的主链路

```mermaid
graph LR
    A[Question] --> B[FastAPI /api/query]
    B --> C[AgentService]
    C --> D[load_schema_catalog]
    D --> E[intent_parser]
    E --> F[schema_retriever]
    F --> G[sql_generator]
    G --> H[sql_validator]
    H --> I[value_validator]
    I --> J[sql_executor]
    J --> K[result_formatter]
```

这条链路背后的原则是：

1. 先做 schema grounding，再做 SQL 生成
2. 先做只读与可执行性校验，再做执行
3. 执行失败返回结构化结果，而不是把异常直接泄露给客户端

---

## 快速开始

### 1. 启动后端

```bash
cd backend
uv sync
cp .env.example .env
uv run dev.py
```

后端默认地址：

```text
http://127.0.0.1:8787
```

健康检查：

```text
http://127.0.0.1:8787/api/health
```

### 2. 启动前端

```bash
cd frontend
pnpm install
pnpm dev
```

前端默认地址：

```text
http://127.0.0.1:4242
```

前端 `/api` 已代理到后端 `http://127.0.0.1:8787`。

### 3. 发起一次查询

请求示例：

```json
{
  "question": "查询近 90 天成交额最高的前 10 个客户"
}
```

当前 API 会返回：

- `sql`
- `params`
- `status`
- `explanation`
- `rows`
- `columns`
- `row_count`
- `execution_summary`
- `error_message`
- `debug`

`status` 当前语义：

- `ready`：真实模型或可执行主路径完成
- `mock`：使用回退生成路径
- `error`：校验、schema 读取或执行阶段失败

---

## 示例输出

```sql
SELECT `customer_name`, SUM(`amount`) AS `total_amount`
FROM `orders`
WHERE `created_at` >= DATE_SUB(CURRENT_DATE, INTERVAL 90 DAY)
GROUP BY `customer_name`
ORDER BY `total_amount` DESC
LIMIT 10;
```

返回结果会在前端工作台中同时展示：

- SQL 文本
- 参数列表
- 表格结果
- 执行摘要
- 调试信息

这使得 SQLAgent 更适合做企业技术评估、prompt/schema 调试和回归验证，而不是只看一句“模型回答”。

---

## 目录结构

```text
SQLAgent/
├─ frontend/
│  ├─ src/
│  │  └─ App.vue
│  ├─ package.json
│  └─ README.md
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ routers/
│  │  ├─ services/
│  │  ├─ agent/
│  │  ├─ rag/
│  │  ├─ validator/
│  │  ├─ database/
│  │  └─ prompts/
│  ├─ tests/
│  ├─ pyproject.toml
│  └─ README.md
├─ docs/
└─ README.md
```

### 后端职责划分

- `routers/`：HTTP 边界与响应合同
- `services/`：服务编排与依赖注入
- `agent/`：LangGraph 状态、节点与流转
- `rag/`：schema catalog、检索、业务语义增强
- `validator/`：SQL 安全校验
- `database/`：执行器、引擎、会话与结果归一化
- `prompts/`：提示词与 few-shot 数据

---

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite |
| 后端 | FastAPI、Pydantic Settings |
| 智能体编排 | LangGraph、LangChain Core |
| 数据访问 | SQLAlchemy Async |
| 默认本地数据库 | SQLite / aiosqlite |
| 测试 | pytest |
| Python 环境 | Python 3.12、uv |

---

## 开发命令

### Backend

```bash
cd backend
uv sync
uv run dev.py
uv run pytest
```

### Frontend

```bash
cd frontend
pnpm install
pnpm dev
pnpm build
```

---

## 文档导航

### 首先阅读

- [`README.md`](./README.md)：项目定位、能力边界、架构与快速开始
- [`docs/NL2SQL_AGENT_IMPLEMENTATION_TODO.md`](./docs/NL2SQL_AGENT_IMPLEMENTATION_TODO.md)：面向实现的工程化待办与优先级

### 补充学习材料

以下文档保留为补充学习资源，不再承担仓库首页主叙事：

- [`docs/BEGINNER_SQLAGENT_ROADMAP.md`](./docs/BEGINNER_SQLAGENT_ROADMAP.md)
- [`docs/BEGINNER_SQLAGENT_4_WEEK_PLAN.md`](./docs/BEGINNER_SQLAGENT_4_WEEK_PLAN.md)
- [`docs/AGENT_LEARNING_TODO.md`](./docs/AGENT_LEARNING_TODO.md)

如果你是第一次接触 Agent / LangGraph / SQLAgent，这些文档仍然有帮助；如果你是来评估项目能力与工程方向，应优先看本 README 与实现路线文档。

---

## 当前路线图

### 已完成

- 前后端工作台与 `/api/query` 查询入口
- schema catalog 加载与 relation-aware schema context
- Stage 1 联表可靠性止血方案
- 只读 SQL 校验、结构化执行结果与错误摘要
- 基于 LangGraph 的多阶段查询主链路

### 进行中

- 更强 schema retrieval 与业务语义覆盖
- 更稳健的 value validation 与 SQL 修复重试
- 更丰富的调试、评测与回归能力

### 后续重点

1. 更可靠的真实执行与结果返回质量
2. 更强的 schema retrieval 与 cross-table reasoning
3. 更严格的 SQL 安全边界与权限治理
4. 更完整的 observability、evaluation 与 regression coverage

---

## 适合的使用场景

- 企业 SQL Agent 方案评估与 PoC
- 中文业务查询工作台
- schema-grounded NL2SQL 实验平台
- join reliability / SQL safety / execution flow 迭代验证
- 面向真实数据库接入前的治理边界设计

---

## 许可证

当前仓库未在本 README 中额外声明许可证信息；如需对外发布，请以仓库实际许可证文件为准。
