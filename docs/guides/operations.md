# 操作与运维指南

状态：Current
最后更新：2026-07-29

本手册面向本地操作者和后续维护者。当前系统是绑定回环地址的预发布工作台，不具备直接公网部署
所需的认证、TLS、服务托管、自动备份或监控能力。开发环境与代码验证见[开发指南](development.md)，
运行拓扑与数据事实来源见[运行架构](../architecture/runtime-architecture.md)和
[数据生命周期](../architecture/data-lifecycle.md)。

## 当前运行边界

运行期必须区分原 PDF、共享页图、ParseRun 私有产物、工作簿草稿和已发布快照。原件、当前解析
运行和已发布版本不得被后台任务覆盖或普通清理。模型原始响应可以用于追溯，但不得包含密钥、
授权头或数据库凭证。

| 资源 | 当前权威位置 | 操作约束 |
| --- | --- | --- |
| 运行状态与版本记录 | MySQL `af_` 表 | 只通过 Alembic 改变 schema，不直接 `DROP` 或 `TRUNCATE`。 |
| 原 PDF 与解析产物 | 本地 `data/documents/` | 原件不可清理；运行产物由 manifest 校验。 |
| 暂存清理数据 | 本地 `data/trash/` | purge 前可回滚，purge 后只能依赖外部完整备份恢复。 |
| 工作簿与发布快照 | 本地文件与 MySQL revision/release | 草稿不能覆盖已发布快照。 |
| 私有评测运行 | 本地 `data/evaluation/runs/` | 可重跑，不得把教材正文、页图或完整响应提交 Git。 |

## 启动与停止

确认 `.env` 指向预期 MySQL 数据库后，显式升级 schema：

```powershell
python -m alembic upgrade head
```

分别在两个终端启动 API/Web 和 Worker：

```powershell
python -m uvicorn axiom_flow.main:app --host 127.0.0.1 --port 8000
python -m axiom_flow.worker
```

使用健康接口确认 API 和数据库装配成功：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

应用启动只校验 schema，不自动迁移。API 和 Worker 是独立进程；停止 Worker 不丢失已入队任务，
但运行中任务需要等待租约恢复。关闭浏览器不会取消任务，取消必须通过工作台或 API 显式提交。
先停止产生新命令的 API，再等待或停止 Worker；不要在进程仍写入时迁移、重建或清理数据。

## 日常检查

1. 确认 API 健康接口成功，Worker 日志没有持续的租约或数据库错误。
2. 提交真实模型任务前核对文档哈希、物理页范围、模型、调用预算、可用空间和回滚位置。
3. 完成运行后验证当前 ParseRun、页面数量、产物摘要和最终 `manifest.json`。
4. 新成功运行只产生候选；必须经过显式选择才成为当前解析事实。
5. 不使用历史 MinerU 输出目录或旧版命令解释当前运行结果。

公开 fixture 回归使用 `python -m evaluation.replay` 和 `python -m evaluation.regression`，不需要
模型密钥。真实教材先运行 `python -m evaluation.preflight`，再使用 `python -m evaluation.benchmark`；
完整输出必须指定到 `data/evaluation/runs/`。

## 开发库重建

受保护工具默认只允许重建测试库，并要求输入完整确认词：

```powershell
python -m axiom_flow.tools.reset_dev_database --database axiom_flow_test
```

工具写入 `data/backups/reset-*.json` 的只是数据库 revision、表名和行数清单，不包含业务数据，
不能作为恢复备份。`--allow-runtime` 只是代码级危险操作开关，不代表运行库已经备份；没有独立的
MySQL 数据备份、产物备份和恢复演练时禁止对运行库执行重建。

## 解析运行清理

旧 ParseRun 只能通过 `axiom_flow.tools.prune_parse_runs` 清理。先设置从 API 或数据库核验得到的完整
值，并执行默认 dry-run：

```powershell
$documentSha = "完整的文档 SHA-256"
$keepRunId = "当前 ParseRun ID"
$targetRunId = "待清理 ParseRun ID"
python -m axiom_flow.tools.prune_parse_runs stage --document-sha256 $documentSha --keep-run-id $keepRunId --run-id $targetRunId
```

审阅计划无误后才允许暂存。暂存会把私有目录移到 trash、删除页面和产物明细，并保留 ParseRun
墓碑及可恢复的 `operation.json`：

```powershell
python -m axiom_flow.tools.prune_parse_runs stage --document-sha256 $documentSha --keep-run-id $keepRunId --run-id $targetRunId --apply
```

记录输出中的 operation ID。需要恢复时先 dry-run，再显式应用：

```powershell
$operationId = "清理操作 ID"
python -m axiom_flow.tools.prune_parse_runs rollback --operation-id $operationId --document-sha256 $documentSha --keep-run-id $keepRunId
python -m axiom_flow.tools.prune_parse_runs rollback --operation-id $operationId --document-sha256 $documentSha --keep-run-id $keepRunId --apply
```

只有复验当前 manifest、页面 API 和清理墓碑后才可 purge。purge 同样先 dry-run；执行后项目内
没有恢复路径：

```powershell
python -m axiom_flow.tools.prune_parse_runs purge --operation-id $operationId --document-sha256 $documentSha --keep-run-id $keepRunId
python -m axiom_flow.tools.prune_parse_runs purge --operation-id $operationId --document-sha256 $documentSha --keep-run-id $keepRunId --apply
```

禁止直接删除 `parse-runs/`、`page-assets/`、`data/trash/` 或对应数据库行。清理工具会拒绝当前、
活动、已审阅、已抽取、已发布引用或目录越界的运行，不得绕过这些保护。

## 故障处置

| 现象 | 首查与恢复 |
| --- | --- |
| API 提示 schema 未初始化或版本落后 | 停止写入进程，核对目标库与备份，再显式执行 `alembic upgrade head`。 |
| 健康接口不可用 | 检查 API 进程、MySQL 连通性、数据库名称和 Alembic revision；不要通过自动建表规避。 |
| 任务长时间不推进 | 检查 Worker 是否运行及其数据库/租约日志；保留任务记录，避免重复手工写表。 |
| OCR 失败或预算耗尽 | 保留脱敏错误和 partial 产物；按评测/计划决定是否重试，不能放宽门槛伪造成功。 |
| manifest 或文件下载失败 | 停止切换当前运行，核对内容哈希、相对路径和产物状态；不要手工修改不可变清单。 |
| 暂存清理后发现问题 | purge 前使用相同文档哈希、当前运行 ID 和 operation ID 执行 rollback。 |
| 已 purge 或运行库已重建 | 只能从操作前的数据库和文件完整备份恢复；JSON 清单不能恢复业务数据。 |

## 生产就绪框架

**OPS-001：生产运维基线未实现。** 以下是平台无关的准入检查表，不是当前部署说明。在该待办
关闭前，API 只能绑定可信本机回环地址，不得直接暴露到公网。

| 能力 | 当前状态 | 生产准入条件 |
| --- | --- | --- |
| 进程与部署 | 仅手工启动 API 和 Worker | 选择部署平台，定义独立服务托管、启动顺序、重启和优雅停止。 |
| 网络与访问控制 | 无认证、TLS 或反向代理契约 | 建立身份验证、授权、TLS、上传限制和可信入口。 |
| 配置与密钥 | 本地 `.env` | 使用受控密钥注入、轮换和脱敏审计，不把密钥写入镜像或日志。 |
| 数据保护 | 无项目级完整备份工具 | 同时备份 MySQL 与 `data/`，定义一致性点、保留周期并完成恢复演练。 |
| 容量与保留 | 只有受保护的手工 ParseRun 清理 | 建立磁盘、数据库、模型预算阈值和经过批准的保留策略。 |
| 可观测性 | 健康接口和基础进程日志 | 增加结构化日志、指标、任务积压/租约/错误告警及审计留存。 |
| 发布与事故 | 只有迁移和 Git 回滚约束 | 建立发布审批、迁移兼容、回滚、事故分级和恢复验证流程。 |

`OPS-001` 关闭前必须先通过 ADR 选择实际部署边界，再以独立计划实现并验证备份恢复、访问控制、
可观测性和发布回滚；仅补写手册不能视为完成。
