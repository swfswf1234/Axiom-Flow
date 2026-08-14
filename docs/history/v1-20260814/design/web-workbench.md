# Web 与 API v1 工作台

设计状态：Accepted
实现状态：Implemented
最后更新：2026-07-30
关联代码：`src/axiom_flow/main.py`、`src/axiom_flow/api/main.py`、`src/axiom_flow/application/reviews.py`、`web/index.html`、`web/style.css`、`web/app.js`
关联测试：`tests/contract/test_design_documents.py`、`tests/integration/test_api.py`、`tests/integration/test_evaluation_api.py`、`tests/contract/test_code_document_mapping.py`
关联 ADR：`docs/adr/0006-persistent-jobs-and-api-v1.md`、`docs/adr/0011-current-parse-run-and-prunable-artifacts.md`、`docs/adr/0018-src-package-and-application-owned-workflows.md`、`docs/adr/0020-document-centric-evaluation-workspace.md`、`docs/adr/0022-neutral-evaluation-snapshots-and-assessments.md`

## 工作台结构

```mermaid
flowchart TD
    SHELL[工作台 Shell]
    LIBRARY[文档库侧栏]
    JOBS[共享任务面板]
    HISTORY[解析历史抽屉]
    PAGES[pages 页面审阅]
    KNOWLEDGE[knowledge 知识候选]
    WORKBOOK[workbook 工作簿发布]
    GRAPH[graph 已发布图谱]
    EVALUATION[evaluation 解析评估]
    ASSESSMENT[单次质量双栏]
    COMPARISON[版本对比三栏]

    SHELL --> LIBRARY
    SHELL --> JOBS
    SHELL --> PAGES
    SHELL --> KNOWLEDGE
    SHELL --> WORKBOOK
    SHELL --> GRAPH
    SHELL --> EVALUATION
    EVALUATION --> ASSESSMENT
    EVALUATION --> COMPARISON
    PAGES --> HISTORY
```

文档库侧栏负责导入和选择文档；任务面板跨视图显示当前任务、进度和取消；主工作区只有
`pages`、`knowledge`、`workbook`、`graph`、`evaluation` 五个 tab。历史抽屉属于页面审阅的辅助界面，不是
独立主视图。

## 视图行为

| 视图 | 当前能力 |
| --- | --- |
| `pages` | 选择当前 ParseRun，按页对照页图与 OCR，切换阅读/Markdown/Blocks，审阅页面并下载产物。 |
| `knowledge` | 提交抽取任务，审阅候选节点、关系和页级证据。 |
| `workbook` | 导出、下载、导入并校验草稿，显式发布最新 revision。 |
| `graph` | 读取并以原生 DOM 展示最新已发布 snapshot。 |
| `evaluation` | 选择文档 case，在单次质量中审阅中性快照 assessment，或以合格基线和中性候选建立 comparison。 |

页面工作区一次只加载一个页面资源，支持页导航、问题筛选、适合宽度和缩放。解析历史默认折叠，
`pruned` 运行只显示摘要。评测视图分为“单次质量”和“版本对比”，各自保存页码、缩放和窄屏活动
面板状态。单次质量在桌面使用来源页图与规范化解析结果双栏，下方显示确定性完整性检查并提交绝对
verdict；`formal_scorecard` 根据页面契约动态显示 0/1/2 维度评分和 critical error。版本对比在桌面
使用来源、冻结基线和候选三栏，下方显示文本、结构、公式、表格/图片与证据差异并提交相对 verdict。
窄屏分别使用 `source/parsed` 和 `source/baseline/candidate` 分段切换，页码导航横向滚动，不压缩多栏。
当前解析结果以规范化 Markdown 原文展示，尚未提供数学公式、表格和内容图片的富文本排版，也没有
通用页面坐标覆盖层、结构化工作簿差异预览或图形布局引擎。后续 [WEB-001](../trackers/todo.md)
先实现安全的 Markdown、LaTeX、表格和内容图片渲染，再根据 block/bbox 数据评估版面级还原；两阶段
均须在实施前建立 B 类计划并冻结依赖、视觉样本和验收门槛。

## HTTP 与文件边界

HTTP 统一使用 `/api/v1`。文档导入返回 `201`；解析和抽取命令返回 `202` Job 资源；Web 轮询任务
状态。当前解析结果只能由带原因的显式选择命令改变。页图、工作簿和产物内容使用后端文件响应，
浏览器不读取数据库、本地绝对路径或百炼凭证。

API 负责请求校验、资源表示和错误翻译，模型任务只由 Worker 执行。文档、任务、审阅和发布操作
全部通过应用服务访问；API 不取得 repository，也不解析数据库保存的本地路径。评测 API 同样只
访问应用服务；Web 不执行 Git 命令、不调用 Provider，也不自动切换当前 ParseRun。
