# ADR 0018：标准包根与应用用例主导工作流

状态：Accepted
日期：2026-07-29
领域：工程治理
决策阶段：v0.3
取代：[ADR 0012](../history/adr/0012-backend-package-boundaries.md)
被取代：—

## 背景

ADR 0012 已完成旧 `backend/app` 收口，但顶层 `backend` 仍同时表达部署位置和 Python 包语义，
API 也直接取得 MySQL repository 完成查询、审阅和发布写入。应用层还直接依赖 HTTPX 异常与
OpenPyXL，导致文档声明的端口边界没有完整落地。

## 决定

1. 生产 Python 包一次性迁移到 `src/axiom_flow`，不保留 `backend` 转发包或双包兼容层。
2. `domain` 只依赖标准库；`application` 负责文档、任务、审阅和发布用例，只依赖领域对象与端口；
   `infrastructure` 实现 MySQL、PDF、文件、百炼和 OpenPyXL 适配器。
3. API 和 Worker 只能调用应用服务或装配根。装配容器不向 API 暴露 repository；文件响应由应用
   服务返回经过边界校验的文件资源。
4. `bootstrap.py` 继续作为唯一装配根。API v1、MySQL schema、Alembic revision、产物 manifest
   和现有运行数据保持兼容。
5. 应用层使用领域级可重试错误，不识别 HTTPX 异常；工作簿文件读写由基础设施 gateway 完成，
   应用服务只负责快照校验与发布规则。

## 后果

启动入口改为 `axiom_flow.main:app` 和 `python -m axiom_flow.worker`，测试、评测、工具、文档和 CI
必须原子更新导入路径。完整主链需要通过 API 与 Worker 从 PDF 导入一直验证到
`KnowledgeRelease`，禁止用直接调用 repository 的测试替代。

## 关联

关联 [ADR 0006](0006-persistent-jobs-and-api-v1.md)、
[ADR 0015](0015-standards-as-governance-source.md)、[运行架构](../architecture/runtime-architecture.md)和
[关闭台账](../trackers/completed.md)。

## 回滚

本变更不修改 schema 或真实数据，可整体回滚代码提交。回滚时恢复旧包路径和装配方式；数据库、
原 PDF、ParseRun 和 manifest 不需要降级。
