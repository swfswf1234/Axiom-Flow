# Web 工作台

设计状态：Accepted
实现状态：Implemented
最后更新：2026-07-27
关联代码：`backend/main.py`、`backend/api/main.py`、`web/index.html`、`web/style.css`、`web/app.js`
关联测试：`tests/test_v03_api.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0006-persistent-jobs-and-api-v1.md`、`docs/adr/0011-current-parse-run-and-prunable-artifacts.md`

工作台由五个工作视图组成：

| 视图 | 用户动作 |
| --- | --- |
| 文档库与任务队列 | 导入 PDF、提交后台任务、查看进度和取消。 |
| 页面质量审阅 | 比对页图、文本、块与风险，给出审阅结论。 |
| 知识候选 | 审阅候选单元、关系和源证据。 |
| 工作簿发布 | 导出、导入、校验、查看差异和显式发布。 |
| 知识图谱 | 浏览已发布节点、关系及原始页证据。 |

Web 层只提交命令与展示查询结果。质量判定、发布校验和版本状态转换必须由后端领域服务
执行。

页面质量审阅使用单页工作区：左侧是带审阅和问题状态的页导航，主区并列显示 PDF 原始页图与
当前 ParseRun 的 OCR；OCR 可切换阅读文本、原始 Markdown 和 Blocks JSON。上一页/下一页、
页码输入、键盘方向键、适合宽度和缩放均只切换一个页面资源，不批量加载全文。运行摘要显示
模型契约、页范围、调用量、manifest 和审阅计数；历史抽屉默认折叠，`pruned` 运行只显示摘要。

新建解析任务必须显式填写包含端点的页范围，并在提交前显示最大模型调用数。解析成功仅加入
历史候选；`POST /documents/{id}/current-parse-run` 才能改变当前结果。当前页下载按整篇 Markdown、
manifest、页 JSON 和供应商响应分组。

v0.3 使用独立的原生 `web/` 静态资源，由 API 进程同源托管；它不依赖旧 `app/` 目录。
HTTP API 以 `/api/v1` 为前缀。解析和知识抽取返回 `202` 任务资源，Web 轮询任务状态；API
进程不得直接执行供应商调用。页图和工作簿下载继续使用文件响应。
