# 计划 2026-08：文档对齐 QED-Engine 模板轮（docs-alignment-round）

状态：Accepted
任务类型：A
最后更新：2026-08-10
关联 ADR：`docs/adr/0015-standards-as-governance-source.md`（标准正文为治理唯一事实源；本轮不新增 ADR）
关联设计：`docs/design/governance-contract-alignment.md`（ALN-008 承接执行治理契约范本对齐；`docs/design/database-schema-ownership.md` 属 ALN-009，仅补登记不展开）
关联 Tracker：`docs/trackers/todo.md`（DOCS-001）
归档判定：W1-W9 全部完成、`pytest tests -q` 与 `ruff check src tests` 全绿、用户逐步确认后关闭并按 ADR 0013 判定 Retain/Delete

## 目标与成功标准

1. Axiom-Flow 文档体系逐节对齐 QED-Engine 模板：索引格式、缺失标准、标准正文与 tracker 结构。
2. 契约测试先行（先红后绿），每节完成后运行定向契约测试与全量门禁，结论记入
   `docs/architecture/project-status.md`（本计划新增）与 todo 证据列。
3. 用户裁决已定：D 类政策按规模分级对齐；ADR 领域枚举保留 5 值；history/ 保持子索引式；
   completed/roadmap 保持现状；README 最小微调。

成功标准：

1. docs/ 各子目录逐节复核完毕（根层 → standards/ → architecture/ → design/ → guides/ →
   trackers/ → plans/ → adr/ → history/）。
2. `pytest tests -q` 全绿 + `ruff check src tests` 无错误；每节执行红色用例先出现后消除。
3. 用户每步确认后推进；根层确认后按 W1-W9 顺序逐节交付。

## 范围与非目标

范围内：

- 文档索引格式对齐（index/guides/trackers/plans/adr 的表格化、状态列、编号位置与活跃计划清单）。
- 缺失标准补齐：governance-contract.md（承接 ALN-008）、cross-project-collaboration.md。
- 标准正文对齐：task-lifecycle（任务层级节、D 类规模分级、C 类措辞）、adr-governance（登记时机/
  章节/审查与优化留痕）、documentation（AGENTS 行、子项目独立文档体系段）。
- project-status.md 新增、todo 类别列与规则节、根层（README/AGENTS/index.md）表述同步。

非目标：

- 不修改 src/、web/、evaluation/ 任何行为代码。
- 不迁移历史：history/ 保持子索引式；本仓库无 learning/，不新增。
- completed/roadmap 保持现有更丰富格式，不退化对齐模板。
- ADR 领域枚举保留 5 值，不做重分类；跨仓库回执不在本轮执行（ALN-008 实现轮承接）。

## 前置条件

- QED-Engine 根仓库文档重构轮已完成（2026-08-10，151 passed），模板稳定可参照。
- 用户已确认范围（全量对齐）、交付形式（计划文档 + todo 登记）与裁决项（D 类规模分级、
  ADR 领域 5 值、history 子索引式、README 最小微调）。
- 工作区未提交变更（ALN-001~009 登记、`docs/design/database-schema-ownership.md`、
  `docs/design/governance-contract-alignment.md`）全程保留不动。

## 工作项

| ID | 工作项 | 状态 |
| --- | --- | --- |
| W0 | 根层：README 重新定位（解析知识，参照 QED-Engine 风格）、requirements.txt 退役、AGENTS 目标同步、docs/index 导航对齐、本计划与 DOCS-001 登记 | ✅ 已完成（2026-08-10：契约守护先行转绿，contract 50 passed；全量门禁无回归） |
| W1 | standards/：新增 governance-contract.md（守护面按本仓库契约测试清单裁剪）与 cross-project-collaboration.md（承接侧流程）；task-lifecycle 增任务层级节、D 类规模分级、C 类措辞；adr-governance 增登记时机/章节/审查留痕节（领域保留 5 值）；documentation 增 AGENTS 行与子项目独立体系段；index 扩 7 行表；守护测试先行 | ✅ 已完成（2026-08-10：契约头六字段补齐 12 文件、新增 test_cross_project_collaboration.py、code-map 登记；contract 52 passed，全量 113 passed 无回归，ruff clean） |
| W2 | architecture/：新增 project-status.md（当前主线、各域状态、维护规则、进场先读）；index 改状态列式；code-map 复核 | ✅ 已完成（2026-08-12：project-status.md 新增并纳入架构守护集合、index 四列表化、AGENTS 进场入口更新、code-map 复核无遗留；contract 52 passed，全量 113 passed 无回归） |
| W3 | design/：index 改状态列式 + 补登 database-schema-ownership、governance-contract-alignment | ✅ 已完成（2026-08-12：index 四列表化并登记 7 份设计文档；两份新文档补齐元数据（关联代码/关联 ADR/关联测试）与 Mermaid 视图、纳入设计守护集合；两个历史既有红修复，contract 54 passed 全绿，全量 115 passed 无回归） |
| W4 | guides/：index 表格化（文档/状态/内容）；development.md 元数据核对；operations.md 保持 | 待开始 |
| W5 | trackers/：todo 加类别列与规则节、存量任务标类别；index 表格化；completed/roadmap 保持现状 | 待开始 |
| W6 | plans/：index 增活跃计划清单（遵守导航守护，不出现计划文件名） | 待开始 |
| W7 | adr/：index 格式对齐（当前决定小节、列名、编号位置文末），历史 ADR 保留分组 | 待开始 |
| W8 | history/ 措辞同步（保持子索引式）；AGENTS.md 路由随新增标准同步 | 待开始 |
| W9 | 门禁收尾：全量 pytest + ruff 全绿；结论入 project-status 与 todo 证据列；用户确认后 DOCS-001 关闭、计划归档 | 待开始 |

## 验证与验收

- 每节先适配契约测试再改正文（先红后绿），失败即暴露漂移。
- 每节完成后运行定向测试 `pytest tests/contract/<该节点对应文件> -q` 与全量
  `pytest tests -q`、`ruff check src tests`。
- 根层完成后运行 `pytest tests/contract -q`（document_structure / markdown_links /
  standard_governance / plan_governance / tracker_governance 定向核对）。
- 每步结果汇报用户，确认后再进下一步；用户抽查各节索引、链接与标准正文。

## 回滚

- 文档改动经 git 回滚；契约测试先行保证漂移早暴露。
- 每步独立小步变更，单步失败不影响其他节；计划与 todo 状态按 task-lifecycle 规则回滚。
- 本计划不含数据、标签或发布操作，无需备份。

## 关闭与归档

- 关闭条件：W1-W9 全部完成、门禁全绿、用户逐节确认并最终确认。
- 归档：关闭后按 ADR 0013 判定 Retain/Delete；DOCS-001 迁移 completed 并记录证据。