# Web 工作台

状态：Current（目标设计）  
最后更新：2026-07-27
关联代码：`backend/app/main.py`、`web/index.html`、`web/style.css`、`web/app.js`  
关联测试：`tests/test_v02_pipeline.py`、`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0004-v02-http-api-boundary.md`

首期界面由五个工作视图组成：

| 视图 | 用户动作 |
| --- | --- |
| 文档库与任务队列 | 导入 PDF、查看处理状态、发起重试。 |
| 页面质量审阅 | 比对页图、文本、块与风险，给出审阅结论。 |
| 知识候选 | 审阅候选单元、关系和源证据。 |
| 工作簿发布 | 导出、导入、校验、查看差异和显式发布。 |
| 知识图谱 | 浏览已发布节点、关系及原始页证据。 |

Web 层只提交命令与展示查询结果。质量判定、发布校验和版本状态转换必须由后端领域服务
执行。

v0.2 使用独立的原生 `web/` 静态资源，由 `backend/app/main.py` 在本地服务同源托管；
它不依赖旧 `app/` 目录。HTTP API 以 `/api` 为前缀，页图和工作簿下载使用文件响应。
