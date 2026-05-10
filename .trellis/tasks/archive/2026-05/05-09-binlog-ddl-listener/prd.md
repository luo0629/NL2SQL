# Schema 变更自动检测与 YAML 同步（方案三：定时轮询 INFORMATION_SCHEMA）

## Goal

当 MySQL 数据库 schema 发生 DDL 变更（ALTER TABLE, CREATE TABLE, DROP TABLE 等）时，系统能自动检测变更并触发 schema 重新同步，更新 business_semantics YAML 文件。通过定时轮询 INFORMATION_SCHEMA 比较 schema 签名，仅在有变更时才触发全量同步。

## What I already know

- 项目使用 asyncmy 驱动连接 MySQL
- 当前 schema sync 逻辑在 `backend/app/rag/schema_sync.py`，通过 SQLAlchemy inspector 读取元数据
- YAML 生成逻辑在 `backend/app/rag/business_semantics.py`，`_load_or_refresh_yaml_overrides()` 负责更新
- 缓存 TTL 默认 300s，在 `backend/app/services/rag_service.py` 中管理
- FastAPI 后端，运行在 8787 端口
- 已有依赖：asyncmy, sqlalchemy, pyyaml, fastapi

## Requirements

- 后台定时轮询 INFORMATION_SCHEMA.TABLES 和 INFORMATION_SCHEMA.COLUMNS
- 计算 schema 签名（表名 + 列名 + 列类型 + 列注释的哈希）
- 签名变更时触发全量 sync_schema_metadata() 更新 YAML
- 更新 YAML 文件的 generated 部分，保留 overrides 部分
- 清除 rag_service 的缓存
- 监听器作为 FastAPI lifespan 后台协程运行
- 异常时记录日志并继续轮询
- 通过配置开关启用/禁用，可配置轮询间隔

## Acceptance Criteria

- [ ] 能检测到 ALTER TABLE / CREATE TABLE / DROP TABLE 等 DDL 变更
- [ ] 仅在 schema 签名变更时才触发全量同步（无变更时不读库）
- [ ] 同步后更新 YAML generated 部分，保留 overrides 部分
- [ ] rag_service 缓存在同步后被清除
- [ ] 作为 FastAPI lifespan 后台协程运行，shutdown 时优雅停止
- [ ] 异常不会导致 watcher 退出，记录日志后继续
- [ ] 通过 schema_watcher_enabled 配置开关控制
- [ ] 轮询间隔可通过 schema_watcher_interval_seconds 配置

## Definition of Done

- Tests added/updated (unit/integration where appropriate)
- Lint / typecheck / CI green
- Docs/notes updated if behavior changes

## Decision (ADR-lite)

**Context**: binlog 方案需要 MySQL 开启 GTID 和特殊权限，配置成本高
**Decision**: 改用定时轮询 INFORMATION_SCHEMA 的方式检测 schema 变更，零额外配置
**Consequences**: 延迟取决于轮询间隔（默认 30 秒），但实现简单、无外部依赖、无需特殊权限

## Out of Scope

- binlog 监听（已排除）
- DML（数据变更）监听
- 实时推送（秒级延迟）
- DDL 结构化解析（提取具体表名/列名变更）

## Technical Notes

- INFORMATION_SCHEMA 是 MySQL 标准功能，任何有 SELECT 权限的用户都能查
- 签名算法：对所有表的列信息排序后取 SHA256 哈希前 16 位
- 需要暴露 rag_service 的缓存清除接口
- 新增配置项：schema_watcher_enabled (bool), schema_watcher_interval_seconds (float)
