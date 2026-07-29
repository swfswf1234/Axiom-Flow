# 开发指南

状态：Current
最后更新：2026-07-29

本手册面向参与 Axiom-Flow 代码与文档开发的人员，维护可重复的环境准备、变更流程、迁移开发和
验证命令。系统运行、数据保护与故障处置见[操作与运维指南](operations.md)。

## 开发环境

前置条件为 Python 3.12、MySQL 8 和 Node.js。MySQL 运行库与测试库必须预先存在且名称不同；
测试会清理测试库中的 `af_` 表，禁止把 `AXIOM_MYSQL_TEST_DATABASE` 指向运行库。

复制配置模板并填写本机值：

```powershell
Copy-Item .env.example .env
python -m pip install -e ".[dev]"
```

`pyproject.toml` 是依赖事实源，`requirements.txt` 只是安装运行依赖的兼容入口。只运行项目而不
参与开发时可使用 `python -m pip install -r requirements.txt`。全部配置字段及默认示例以
`.env.example` 为准；API key 和数据库密码不得进入源码、命令参数、日志、工作簿或评测报告。

首次启动前按[操作与运维指南](operations.md#启动与停止)显式升级 schema，并分别运行 API/Web
与 Worker。浏览器入口为 `http://127.0.0.1:8000`。

## 变更流程

1. 按 [AGENTS.md](../../AGENTS.md) 定位当前计划、架构、设计、ADR、代码映射和定向测试。
2. 按[任务生命周期](../standards/task-lifecycle.md)分类；涉及边界、公开接口、持久化或解析路由时
   先更新 ADR。B/C/D 与非平凡 A 类建立单类型 Accepted 计划，简单 A 类不创建计划文件。
3. 计划明确范围、验证、回滚和完成条件后再执行；C 实验、B 实现和 D 正式操作分别关闭。
4. 同步 `code-map.md`、模块 `DesignRef`、活跃设计和适用的语义门禁。
5. 通过定向、回归和关闭门禁后，按[文档规范](../standards/documentation.md)选择性保留或删除计划；
   可复现失败登记到[待做任务](../trackers/todo.md)。

Rudin 页码、预算和采纳门槛属于专项事实，只查[解析评测设计](../design/evaluation-governance.md)、
[ADR 0010](../adr/0010-qwen-ocr-only-rudin-trial.md)、[20 页试跑报告](../../evaluation/reports/rudin-qwen-ocr-20-v1.md)
和[后续候选任务](../trackers/todo.md)，不作为通用开发命令。

## 数据库迁移开发

`alembic.ini` 只保存迁移路径和日志配置，数据库 URL 由环境变量注入。先按日期和当日顺序确定
revision ID，再生成空迁移骨架：

```powershell
python -m alembic revision --rev-id 20260729_0005 -m "说明本次结构变更"
```

人工实现并审阅 `upgrade()` 与 `downgrade()`，同步数据生命周期、code-map 和迁移测试。项目没有
SQLAlchemy 声明式模型元数据，禁止使用 `--autogenerate`。应用启动只校验 schema，不代替显式
迁移。执行迁移和受保护重建前必须阅读[操作与运维指南](operations.md)。

## 测试与检查

评测回答外部模型或候选方案是否值得采纳；测试保护已经采纳的确定性契约，两者不能替代。

| 变更范围 | 最低定向门禁 |
| --- | --- |
| 文档入口、索引或链接 | `python -m pytest tests/test_document_structure.py tests/test_markdown_links.py -q` |
| 受管代码或 DesignRef | `python -m pytest tests/test_code_document_mapping.py -q` |
| 架构或依赖方向 | `python -m pytest tests/test_architecture_documents.py tests/test_architecture_dependencies.py -q` |
| 设计接口、状态或关键常量 | `python -m pytest tests/test_design_documents.py -q` |
| Web 页面或交互 | 适用 API 测试及 `node --check web/app.js` |
| 共享模型、状态机或跨领域流程 | 定向测试后追加全量 `python -m pytest -q` |

集成测试连接 `AXIOM_MYSQL_TEST_DATABASE`，自动迁移并在测试前后清空 `af_` 表；运行库与测试库
相同会立即失败。模型测试使用假供应商，GitHub CI 不调用百炼。

提交前执行适用于完整差异的关闭门禁：

```powershell
python -m ruff check .
python -m pytest -q
node --check web/app.js
git diff --check
```

没有修改 Web 时可省略 JavaScript 语法检查。测试分层、关闭条件和失败归档规则以
[任务生命周期](../standards/task-lifecycle.md)为准，架构与设计同步触发项以
[代码与文档追溯规范](../standards/code-document-traceability.md)为准。

## 开发问题定位

| 现象 | 检查方式 |
| --- | --- |
| 安装后找不到 `axiom_flow` 或 `evaluation` | 重新执行可编辑安装，确认当前 Python 为 3.12 且命令在仓库根目录运行。 |
| 测试提示运行库与测试库相同 | 修正 `.env` 中两个数据库名称，禁止绕过隔离检查。 |
| 测试提示 schema 未初始化 | 确认 MySQL 可连接；测试 fixture 会创建并迁移测试库。运行库迁移按运维手册执行。 |
| 文档映射失败 | 从报错模块检查文件头、`code-map.md`、关联设计和测试是否同时更新。 |
| 架构或设计语义测试失败 | 先核对代码真实类型、枚举和常量，再同步正文与 Mermaid，不能只修改断言。 |
