# 按设计方案重构 NL2SQL Agent

## Goal

根据 `C:\Users\nefli\Downloads\NL2SQL_Agent_方案设计.md` 重构当前 SQLAgent，把上一轮不准确且偏复杂的 plan / SemanticQuery 路线改为更直接、更可验证的 LangGraph 链路：`intent_parser → schema_retriever → sql_generator → sql_validator(EXPLAIN) → sql_executor → result_formatter`。目标是提升自然语言理解准确率、动态 schema 真实性、SQL 可靠性和执行安全性，同时保留当前项目已配置的 LLM provider/model，不按设计文档中的模型推荐更换。

## What I already know

* 用户认为当前 plan 路线错误且不准确。
* 用户提供的新设计强调：先审题、动态拉取真实 MySQL schema、生成 SQL、用安全校验 + EXPLAIN 预检、执行查询、结果自然语言格式化。
* LLM 仍使用当前项目配置，不切换到文档中 GPT/Claude/Qwen 等推荐模型。
* 冗余流程可以删除，项目应以准确、简单、可执行为优先。
* 当前项目已有 LangGraph、真实数据库执行、SQL validator、schema sync、前端查询结果展示等基础。

## Assumptions (temporary)

* 当前生产目标数据库以 MySQL 兼容协议为主；默认 SQL 方言应按 MySQL 处理。
* 可以删除或旁路上一轮引入的 `SemanticQuery` / `sql_plan` 复杂兼容路径，只保留必要 debug 信息。
* 第一阶段目标是可用且简洁的闭环，不追求多模型路由、向量检索、LangSmith 或完整反馈系统。

## Open Questions

* 当前无阻塞问题。

## Requirements

* 重构 LangGraph 节点为设计文档中的简化链路。
* `intent_parser`：LLM 解析用户意图，并从真实表名列表中选择 1–4 张相关表；程序侧过滤不存在的表。
* `schema_retriever`：基于真实数据库 schema 生成相关表结构上下文，优先包含字段类型、nullable、默认值、字段注释、表注释、主键/外键信息。
* `sql_generator`：基于 intent、schema_context、上一轮 validation_error 生成 MySQL 只读 SELECT SQL；继续使用当前配置的 LLM。
* `sql_validator`：执行安全校验，并使用 `EXPLAIN` 进行 MySQL 语法/字段/表预检；失败时带错误信息重试生成，最多 3 次。
* `sql_executor`：只执行通过验证的 SELECT 查询，保留只读校验、超时和行数限制。
* `result_formatter`：把结构化结果转换为自然语言回答；空结果和失败结果返回友好说明，不泄露内部细节。
* 采用“替换后删除”策略：先把 LangGraph 主路径替换为新设计，测试通过后删除不再引用的 `SemanticQuery/sql_plan/schema_linking/value_linking/join_path` 冗余代码与对应旧测试。
* 前端继续可以展示 SQL、结果表格、自然语言回答和错误状态。

## Acceptance Criteria (evolving)

* [ ] LangGraph 主链路符合 `intent_parser → schema_retriever → sql_generator → sql_validator → sql_executor → result_formatter`。
* [ ] LLM provider/model 配置保持现状，未按设计文档替换。
* [ ] SQL 生成使用真实 schema context，而不是硬编码 schema 或静态 mock 表结构。
* [ ] SQL 验证在执行前包含安全校验和 EXPLAIN 预检。
* [ ] 验证失败时最多重试 3 次，并把错误反馈给 SQL 生成节点。
* [ ] 只执行 SELECT 查询，危险 SQL 被阻止。
* [ ] 查询结果返回结构化 `rows/columns/row_count`，并返回自然语言 `final_answer` 或等价说明。
* [ ] 相关后端测试和必要前端构建通过。

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Backend full or focused regression tests pass
* Frontend build passes if API/response handling changes
* Specs updated if new executable contracts are established
* Rollback considered because this is a pipeline replacement

## Technical Approach

* Graph 主路径重构为：`intent_parser → schema_retriever → sql_generator → sql_validator → sql_executor → result_formatter`。
* `intent_parser` 先通过真实 schema catalog 获取表名列表，再让当前配置 LLM 生成意图说明和候选表，程序侧过滤 hallucinated table。
* `schema_retriever` 只为候选表构建完整 schema context，避免把全库塞进 prompt。
* `sql_generator` 直接消费 `intent/schema_context/validation_error/previous_sql/retry_count`，生成 MySQL SELECT SQL；不再经由 SQL plan 或 SemanticQuery 渲染。
* `sql_validator` 先做现有只读安全校验，再执行 EXPLAIN 预检；预检失败进入最多 3 次重试。
* `sql_executor` 继续复用现有 `SQLExecutor`，保留行数限制、序列化和错误脱敏。
* `result_formatter` 生成自然语言最终回答，同时保留 SQL 和结构化结果供前端展示。
* 清理不再引用的冗余模块、状态字段和测试。

## Decision (ADR-lite)

**Context**: 上一轮 `SemanticQuery/sql_plan/schema_linking/value_linking/join_path` 链路过长，用户认为路线不准确，且当前目标是按设计文档建立更直接的真实 MySQL NL2SQL 闭环。

**Decision**: 采用“替换后删除”：主链路按设计文档替换为六节点闭环，确认测试通过后删除不再引用的冗余流程。LLM 配置保持当前项目设置，不按设计文档更换。

**Consequences**: 项目链路会更简单、更贴近真实 schema 和 EXPLAIN 校验；代价是一次性改动较大，需要同步更新后端测试和前端响应契约。

## Out of Scope

* 不更换或新增 LLM provider/model。
* 不引入 LangSmith、Milvus、pgvector 或多模型路由。
* 不实现用户反馈 👍/👎 闭环。
* 不要求自动为业务表补充 COMMENT，只要求读取和利用已有 COMMENT。
* 不做写库能力，生产建议只读用户作为部署要求记录即可。

## Implementation Plan (small PRs)

* PR1: 重构 AgentState 和 graph 节点为六节点主链路，保留当前 API 响应兼容。
* PR2: 实现动态表名选择、相关表 schema context、直接 SQL 生成 prompt 和 validation retry。
* PR3: 接入 EXPLAIN 预检、执行结果格式化和前端响应字段适配。
* PR4: 删除不再引用的 SemanticQuery/sql_plan/schema_linking/value_linking/join_path 冗余模块与旧测试，补充新链路测试。

## Technical Notes

* 设计文档路径：`C:\Users\nefli\Downloads\NL2SQL_Agent_方案设计.md`。
* 当前主链路在 `backend/app/agent/graph.py`：`query_understanding → retrieve_schema → schema_linking → value_linking → join_path_planning → build_semantic_brief → build_semantic_query → sql_planning → generate_sql → validate_sql → execute_sql → finalize_response`。
* 当前 state 在 `backend/app/agent/state.py`，包含 `query_understanding/query_schema_plan/schema_linking/value_links/join_path_plan/business_semantic_brief/semantic_query/execution_gate/sql_plan/sql/sql_params/rows/debug_trace` 等字段。
* SQL 生成、校验、执行、响应主要涉及 `backend/app/agent/nodes.py`、`backend/app/validator/sql_validator.py`、`backend/app/database/executor.py`、`backend/app/services/agent_service.py`、`backend/app/routers/query.py`、`backend/app/schemas/query.py`。
* 前端响应消费在 `frontend/src/App.vue`。
* 冗余候选：`schema_linking/join_path_planning/build_semantic_brief` 多数只是铺平 `query_schema_plan`；`value_linking` 可前移或删除；`semantic_query/sql_planning/sql_plan` 不应继续作为主路径。
* 直接删除存在测试和引用连带影响；更安全路线是先替换 LangGraph 主路径，再删除不再引用的模块和测试。
