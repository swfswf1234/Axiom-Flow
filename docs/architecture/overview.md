# 系统总览

状态：Current（v0.2 已实施）  
最后更新：2026-07-27
关联代码：v0.2 受管实现及 Legacy 边界见 `docs/architecture/code-map.md`  
关联测试：`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0002-parser-routing-and-provider-boundary.md`

Axiom-Flow 是本地单用户的技术文档质量与知识审阅工作台。首期交付从 PDF 导入、
解析、页级质量审阅、教学语义单元审阅、Excel 发布到知识图谱浏览的闭环。

本页描述已验证的 v0.2 当前事实。v0.3 已接受目标单独见 `v03-target.md`；持久任务、API v1
和追加式抽取历史在实现与验证完成前不得写成本页的现有能力。

```mermaid
flowchart LR
    A[PDF 原件] --> B[处理任务]
    B --> C[解析路由]
    C --> D[规范化内容和页级证据]
    D --> E[质量初筛与人工复核]
    E --> F[知识候选抽取]
    F --> G[Excel 审阅工作簿]
    G --> H[版本校验与显式发布]
    H --> I[已发布知识库与图谱]
```

系统不把任何解析器或大模型的原始输出当作领域事实。解析结果先归一化为页、内容块、
源证据和质量报告，再供审阅和知识抽取使用。

运行数据使用 MySQL 的 `af_` 前缀表；原文件与可追溯产物保存在本地数据目录；Excel 是人工编辑与
发布的主要入口。后续聊天、图片问答、练习和学习进度只能读取已发布知识版本。
