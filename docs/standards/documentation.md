# 文档规范

状态：Current
最后更新：2026-07-29
治理对象：文档分类、写作、元数据、索引、命名、归档与删除
依据 ADR：`docs/adr/0013-selective-history-retention.md`、`docs/adr/0015-standards-as-governance-source.md`、`docs/adr/0016-remove-document-templates.md`、`docs/adr/0017-consolidated-task-ledgers-and-roadmap.md`
关联测试：`tests/contract/test_document_structure.py`、`tests/contract/test_markdown_links.py`、`tests/contract/test_standard_governance.py`

## 目的与边界

本标准规定每类文档保存什么事实、采用什么元数据，以及何时归档或删除。具体任务状态查
[任务生命周期](task-lifecycle.md)，ADR 状态查 [ADR 治理](adr-governance.md)，代码映射查
[代码与文档追溯](code-document-traceability.md)。指南只维护可重复操作；新文档遵守对应 standard，
再参考最近一份仍有效的同类文档组织内容。

## 强制规则

### 文档分类与事实边界

| 位置 | 唯一职责 |
| --- | --- |
| 根 `README.md` | 面向用户和新开发者的项目定位、能力、技术栈和快速启动。 |
| `docs/index.md` 与各目录 `index.md` | 只导航当前文件，不保存正文事实。 |
| `architecture/` | 当前系统结构、边界、运行拓扑、数据生命周期和代码映射。 |
| `design/` | 当前流程、接口、状态、失败语义、质量门槛和验收契约。 |
| `adr/` | 影响长期约束的决定、理由、后果和取代关系。 |
| `standards/` | 工程治理规则。 |
| `guides/` | 当前可重复执行的开发与运维步骤。 |
| `plans/` | 已批准且尚未关闭的短期执行合同。 |
| `trackers/` | 全部未关闭任务、简短关闭台账和无状态能力路线图。 |
| `history/` | 选择性保留的长期审计证据和旧基线 Git 锚点。 |

一个事实只设一个维护位置，其他文档使用链接。实验参数、结果和评分保存在 `evaluation/`，标准
不得复制操作命令、设计契约或 ADR 决策理由。仓库不维护 Markdown 文档模板目录。

### 写作、命名与索引

- 中文说明使用短句和明确主语；标识符、API 字段和外部协议名称保留英文。
- 文件名使用小写英文和连字符；活跃架构、设计、标准和指南使用稳定名称，不绑定产品版本。
- 文档目录入口统一为小写 `index.md`；`docs/**/README.md` 禁止存在，根 README 是唯一例外。
- 内部链接显式指向文件或 `index.md`，不依赖托管平台目录解析。
- Mermaid 图与说明在同一正文维护，不提交由 Mermaid 派生的 PNG、SVG 或第二份图源。

### 元数据

- 架构和设计声明设计状态、实现状态、最后更新、关联代码、关联测试和关联 ADR。
- 标准声明状态、最后更新、治理对象、依据 ADR 和关联测试，并使用统一公共章节。
- 计划、ADR 的字段和值分别由任务生命周期和 ADR 治理规定。
- 指南、索引和 tracker 至少声明 `状态` 与 `最后更新`。
- 架构/设计的设计状态只允许 `Draft`、`Proposed`、`Accepted`、`Rejected`、`Superseded`、
  `Historical`；实现状态只允许 `Not Started`、`In Progress`、`Implemented`、`Verified`、
  `Blocked`、`Completed`。

`Implemented` 表示实现和本地定向门禁完成；`Verified` 还要求适用全量与远端门禁通过；Blocked
必须声明证据、恢复条件和责任位置。

创建新文档时，standard 的字段、状态和章节规则优先于任何现有示例。参考同类文档只能借用组织
方式，必须重新确认编号、状态、关联、范围和项目事实。

### 归档与删除

- Rejected/Superseded ADR 永久进入 `history/adr/`，具体路径和取代关系由 ADR 治理规定。
- 关闭计划只有在记录已执行 D 类数据操作、迁移/发布里程碑、事故复盘或不可替代外部证据时进入
  `history/plans/<year-month>/`；其他计划在事实同步且 Git 锚点有效后删除。
- 实验样本、结果和评分留在 `evaluation/`，不复制实验计划；失效指南默认删除，旧操作从 commit
  或 tag 恢复。
- 被整体替换的系统只在 `history/baselines/` 保存范围、失效原因和不可变 Git commit 摘要，不
  复制旧代码或文档树。
- 选择性保留的历史正文保持当时结论，只允许补充 Historical 声明、反向关系或修复链接。

## 执行与门禁

- 新建或移动文档前先确认其唯一事实归属；删除前搜索全部引用并验证 Git 锚点可读。
- 仍含唯一事实、决策依据或审计证据的文件必须归档；精确重复、空草稿或已有高优先级承接事实的
  文件可以删除。
- 可自动判断的目录、元数据和链接规则由关联测试守护；内容准确性、唯一事实和审计价值由计划中
  声明的人工审阅负责。
- 文档结构变更至少运行标准治理、文档结构、Markdown 链接和代码映射测试；当前命令查开发指南。

## 变更与取代

改变文档分类、事实归属、强制元数据、索引入口或归档条件时必须先新增 ADR。措辞、勘误、链接和
不改变语义的结构整理可直接修改。活跃标准和指南不保留版本副本；旧内容按本节规则进入 History
或从 Git 恢复。
