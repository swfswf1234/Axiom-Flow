# 当前运行架构

设计状态：Accepted
实现状态：In Progress
最后更新：2026-07-29
关联代码：`backend/domain/models.py`、`backend/application/ports.py`、`backend/bootstrap.py`、`backend/infrastructure/mysql.py`、`backend/api/schemas.py`、`backend/migrations/versions/20260727_0002_v03_jobs_and_history.py`
关联测试：`tests/test_architecture_documents.py`、`tests/test_architecture_dependencies.py`、`tests/test_v03_jobs.py`、`tests/test_v03_api.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0006-persistent-jobs-and-api-v1.md`、`docs/adr/0007-versioned-domain-records.md`、`docs/adr/0012-backend-package-boundaries.md`

本文件同时记录 Accepted 架构约束和当前实现符合度。运行调用图描述进程间实际交互；代码依赖图
描述允许的 Python 包依赖方向，两者不能互相替代。

API、Worker 和其他模块的主设计关联以[代码映射](code-map.md)为准；它们在本文件中只作为运行
拓扑和符合度证据，不改变各自 DesignRef。

## 运行拓扑

```mermaid
flowchart LR
    U[用户]
    WEB[Web 工作台]
    API[API v1]
    WORKER[独立 Worker]
    APP[应用服务]
    MYSQL[(MySQL af_ 表)]
    FILES[(本地产物)]
    BAILIAN[阿里百炼]

    U --> WEB
    WEB -->|查询与命令| API
    API -->|任务入队| MYSQL
    API --> APP
    API -.->|ARCH-001 当前直连| MYSQL
    WORKER -->|领取、心跳、进度| MYSQL
    WORKER --> APP
    APP --> MYSQL
    APP --> FILES
    APP --> BAILIAN
    API -->|文件响应| FILES
```

API 和 Worker 是两个独立进程。长任务先进入 MySQL 队列，Worker 通过租约领取并调用应用服务；
本地文件保存原 PDF、页图、模型诊断和不可变解析产物。API 不执行模型长任务。

## 代码依赖方向

```mermaid
flowchart TD
    API_PKG[api]
    WORKER_PKG[worker]
    BOOTSTRAP[bootstrap]
    APPLICATION[application]
    INFRASTRUCTURE[infrastructure]
    DOMAIN[domain]

    API_PKG --> BOOTSTRAP
    API_PKG --> APPLICATION
    WORKER_PKG --> BOOTSTRAP
    WORKER_PKG --> APPLICATION
    BOOTSTRAP --> APPLICATION
    BOOTSTRAP --> INFRASTRUCTURE
    INFRASTRUCTURE --> APPLICATION
    INFRASTRUCTURE --> DOMAIN
    APPLICATION --> DOMAIN
```

- `domain` 定义状态、稳定任务视图和领域异常，不依赖框架、供应商或外层包。
- `application` 定义任务、工作簿用例和供应商/pipeline 端口，不导入基础设施适配器。
- `infrastructure` 实现 MySQL、文件产物、PyMuPDF、百炼和 OpenPyXL 适配。
- `bootstrap.py` 是唯一装配根；API 和 Worker 不直接导入 `infrastructure`。
- `api` 负责 HTTP 校验、资源表示和错误翻译；`worker` 负责租约执行循环。

## 能力与运行职责

| 业务能力 | 当前职责 | 当前实现所有者 |
| --- | --- | --- |
| Document | PDF 导入、内容标识、文档状态与当前解析运行选择 | `PDFPipeline`、`MySQLRepository`、API |
| Parsing | 页面渲染、OCR、规范化内容、来源证据和解析产物 | `PDFPipeline`、`VisionProvider`、产物适配器 |
| Quality Review | 页面风险数据、人工审阅状态和重解析请求 | API、`MySQLRepository` |
| Knowledge | 候选节点、关系、证据、审阅状态和发布快照 | `KnowledgeProvider`、`MySQLRepository` |
| Workbook | Excel 导出、导入校验、草稿版本和显式发布 | `WorkbookService`、`MySQLRepository` |

| 运行职责 | 边界 |
| --- | --- |
| Web/API | 展示资源、接收命令和返回文件，不执行模型任务。 |
| Jobs/Worker | 幂等入队、租约、心跳、重试、取消和恢复，不解释 PDF 或知识语义。 |

## 架构符合度

| Accepted 约束 | 当前状态 | 证据与跟踪 |
| --- | --- | --- |
| Domain 和 Application 不反向依赖外层 | 符合 | `tests/test_architecture_dependencies.py` |
| API/Worker 不直接导入基础设施 | 符合 | `tests/test_architecture_dependencies.py` |
| `bootstrap.py` 是唯一装配根 | 符合 | `backend/bootstrap.py` |
| API 只经应用用例访问业务能力 | 部分符合 | **ARCH-001**：API 仍从容器取得 repository，并直接完成部分查询、审阅和发布写入；见[待做任务](../trackers/todo.md)。 |

ARCH-001 关闭前，本文件保持 `实现状态：In Progress`。修复必须通过独立 Backend 计划增加应用
服务，不能在文档对齐任务中静默改变运行行为。
