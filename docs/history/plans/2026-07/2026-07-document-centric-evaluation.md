# 文档中心解析评测基线

状态：Completed
关闭结果：Achieved
任务类型：B
最后更新：2026-07-30
关联 ADR：`docs/adr/0019-public-fixture-and-private-benchmark-boundary.md`、`docs/adr/0020-document-centric-evaluation-workspace.md`
关联设计：`docs/design/evaluation-governance.md`、`docs/design/web-workbench.md`
关联 Tracker：`docs/trackers/todo.md`（EVAL-001）
归档判定：Retain；作为 v0.3 文档中心评测接口、数据布局和关闭门禁的实施依据。

## 目标与成功标准

建立按文档组织的解析评测工作区，使同一来源的冻结 main 基线与候选 ParseRun 可独立保存、逐页
比较、人工裁决并在 Web 中对照。成功标准是 CLI、Application、API 与 Web 共用同一契约，公开
fixture 完成迁移，评测专项、API、架构、文档映射和全量本地门禁通过。

## 范围与非目标

范围包括安全可读命名、文档 case、ParseRun 快照、完整性校验、结构化差异、人工评审、报告、
开发 CLI、API 和第五个 Web 视图。比较覆盖 Markdown、blocks/阅读顺序、公式、表格、图片、证据
bbox、manifest 与运行元数据。

本计划不执行 Rudin 真实 OCR，不决定模型/提示词优劣，不处理知识抽取、工作簿或发布，不清理
DATA-002 数据，不创建标签或 Release，也不自动推送远端。

## 前置条件

- ADR 0019 的公开/私有数据边界继续有效。
- 基线捕获必须提供干净 `main` 修订证据；候选捕获必须记录工作树状态。
- 生产 ParseRun 必须已完成、产物状态可用且 manifest 可校验。
- 私有 Rudin 目录只迁移 manifest 和脱敏报告，PDF、页图和完整响应不得进入 Git。

## 工作项

- [x] 接受 ADR 0020，冻结目录、快照、结论和进程边界。
- [x] 实现应用评测用例和本地文件工作区适配器。
- [x] 实现开发 CLI，所有真实运行只提交并执行生产 Job。
- [x] 将公开 fixture 与 Rudin 私有元数据迁入文档中心目录。
- [x] 增加 API 资源和 Web `evaluation` 对照视图。
- [x] 补齐命名、快照、比较、API、Web、架构与文档映射测试。
- [x] 运行 Ruff、全量 Pytest、JavaScript 语法和差异检查。

## 验证与验收

定向测试覆盖中文/非法字符/路径逃逸与哈希错配、原子写和损坏 JSON、硬链接回退、源 ParseRun
删除后的快照完整性、脏基线拒绝、页范围错配、缺页及各内容维度差异、无金标结论限制、全部变化
页面待审阅、API 无绝对路径泄漏和 Web 三栏/窄屏切换。

适用回归包括公开 fixture replay、现有任务/产物/API 测试、架构依赖、DesignRef 映射、Markdown
链接和全量 Pytest。人工验收只检查桌面与窄屏布局、中文可读性和同页对照，不据此宣称 OCR 质量。

关闭证据：评测专项 29 tests 与隔离数据库全量 119 tests 通过；Ruff、`node --check`、文档契约、
`git diff --check` 和 CLI case 列表通过；Playwright 复用本机 Edge 验证 1440px 三栏与 390px 分段
布局，无水平溢出或面板重叠。

## 回滚

回滚时删除新增评测应用/API/Web 能力并恢复旧公开 fixture 路径；生产 MySQL、内容寻址文档和
ParseRun 不需迁移。已经生成的 `data/evaluation/` 快照保持可读，可由独立 D 类数据计划在确认
精确目标和备份后清理。

## 关闭与归档

全部自动门禁和人工界面检查通过后写入 `关闭结果：Achieved`，从 todo 原子迁移到 completed，
并按归档判定移入 `docs/history/plans/2026-07/`。若主链契约无法支持独立快照则以 Partial 关闭并
登记新的架构偏差，不能用直接 Provider 调用规避。
