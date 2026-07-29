# ADR 0012：Backend 包边界一次性收口

状态：Superseded
日期：2026-07-28
领域：工程治理
决策阶段：v0.3
取代：—
被取代：[ADR 0018](../../adr/0018-src-package-and-application-owned-workflows.md)

## 背景

v0.3 已建立 `domain/application/infrastructure/api/worker` 目录，但配置、仓储基类、PDF 流水线、
供应商、产物和工作簿仍集中在 `backend/app/`。`V03Store` 继承旧 `MySQLStore`，应用任务直接创建
基础设施供应商，API 也直接装配仓储。这使文档宣称的依赖方向与运行代码不一致，继续保留转发层
只会把阶段性兼容路径固化为长期入口。

## 决定

删除整个 `backend/app/`，不提供 `backend.app.*` re-export。内部 Python 导入一次性迁移：

- `backend/domain/` 保存不依赖框架的状态、值对象与错误。
- `backend/application/` 保存用例、策略和端口；禁止导入 API 与基础设施适配器。
- `backend/infrastructure/` 保存配置、MySQL、PyMuPDF、文件产物、百炼和 OpenPyXL 实现。
- `backend/bootstrap.py` 是唯一装配根；API 和 Worker 从装配根获得应用服务。
- `backend/main.py` 是唯一 ASGI 入口，启动命令改为 `backend.main:app`。

MySQL 仓储合并为单一适配器，不再以版本号命名或继承旧仓储。解析执行由应用层端口描述，现有
PyMuPDF/百炼/文件实现作为基础设施 pipeline 注入。HTTP `/api/v1`、Alembic `20260728_0004`、
内容寻址目录、manifest v1/v2 和现有运行数据保持兼容。

## 后果

测试和工具必须同步更新导入路径；旧的 `backend.app.main:app` 启动命令立即失效。代码文档映射
和新增依赖方向测试共同守护边界。v0.3 标签表示工程链路基线，Rudin 公式 OCR 的质量门禁仍保持
阻塞，不因架构收口而改写评测结论。

## 回滚

本变更作为独立提交，可整体回滚到收口前提交；它不包含 schema 迁移或运行数据写入。回滚时
恢复旧 Python 导入和启动命令即可，0004 数据库与当前解析产物无需降级。
