# ADR 0003：文档治理体系对齐 QED-Tracker 成熟范本

状态：Accepted
最后更新：2026-09-11
关联代码：无（文档治理，不映射具体模块）
关联测试：`tests/contract/test_standard_governance.py`、`tests/contract/test_tracker_governance.py`、`tests/contract/test_document_structure.py`

## 背景

Axiom-Flow v2 文档体系（V2-015 范本对齐）已建立 architecture/ 固定文档集与契约测试守护，
但治理层与已成熟的 QED-Tracker 存在差距：

1. 本仓库 `docs/standards/documentation.md`（51 行）只覆盖分类、命名、元数据、归档；缺少
   QED-Tracker `doc-governance.md` 已固化的**确认状态（暂定/已确认）与冲突优先级**、文档生命
   周期、代码与文档追溯、版本末期文档整理、关闭计划 Retain/Delete 两态判定。
2. 标准元数据不统一：现为标准只有 `状态` + `最后更新` + `## 目的`；范本要求
   `状态/确认状态/最后更新/治理对象/依据/关联测试` 与统一公共章节。
3. 本仓库无 `standards/local-dev.md`，本地机器绑定事实散落在 `guides/development.md`「环境」节。
4. 本仓库无 `trackers/project-status.md` 状态快照，进场需从 todo/completed 拼凑现状。
5. `docs/design/index.md` 缺「管/不管」职责登记；design 三态未按 ADR 0002 显式核对。

QED-Tracker 已通过 ADR 0003/0004/0008 完成同类对齐，其 `doc-governance.md` 为三项目复用范本。

## 决定

1. **`documentation.md` 改名 `doc-governance.md`**（对齐 QED-Tracker 与根仓库命名），并扩写：
   确认状态与冲突优先级、文档生命周期、代码与文档追溯、版本末期文档整理、归档与删除
   （含关闭计划 Retain/Delete 两态判定）；适配本仓库事实（`task-lifecycle.md` 独立保留、
   `tests/contract/` 为治理守护、ARCH-010 为版本末期整理长期任务）。
2. **新增 `standards/local-dev.md`**：登记机器标识（本机 UUID `2C6ECD2C-BBEE-11ED-8A95-F0D4154ABBA8`）、
   Python 环境、服务端口速查与常见误判注记；明确本地绑定事实与可移植配置边界。
3. **新增 `trackers/project-status.md`**：作为「30 秒现状」单一入口，登记服务状态、当前主线与
   维护规则；`trackers/index.md` 与 `AGENTS.md` 标准映射同步登记。
4. **标准统一元数据与公共章节**：各标准声明 `状态/确认状态/最后更新/治理对象/依据/关联测试`，
   采用「目的与边界 / 强制规则 / 执行与门禁 / 变更与取代」公共章节；首批 6 份标准均登记为
   **已确认**（2026-09-11 经用户确认）。
5. **AGENT 流程与边界正文落位**：`docs/guides/development.md` 增「AI 开发工作流程」六步节
   （进场读必读 → 定级 → 立项 → 实现 TDD → 验证 → 收尾）与「标准映射机制」说明；
   `AGENTS.md` 标准映射、任务路由、完成检查指向新文件。
6. **契约测试同步**：`test_standard_governance.py`、`test_tracker_governance.py`、
   `test_document_structure.py` 更新标准集合、追踪器集合与元数据契约；全库活跃引用改名清扫。
7. **design 三态梳理**：`docs/design/index.md` 增加每篇设计的「管/不管」职责登记；
   `af-books-sync.md` 属 ADR 0002 存量 Draft 豁免，就地保留；本轮不拆数据库文档。

## 后果

- `standards/` 由 5 份变为 6 份（doc-governance、adr-governance、task-lifecycle、testing、
  cross-project-collaboration、local-dev + index），规则体系与 QED-Tracker 对齐且保留本仓库
  已有的独立 `task-lifecycle.md`。
- `trackers/` 由 4 份变为 5 份（补 project-status）。
- `tests/contract/` 白名单、DesignRef 与链接同轮更新；全库引用改名清扫（含修正 ADR 0002 指向
  根仓库 ADR 0011 的失效链接，现位于 `history/adr/v0.1/`）。
- 好处：目录与状态即可信度标签，确认状态、生命周期与归档判定成文，跨仓库对照成本降低。
- 成本：改名带来一轮引用与测试同步；标准正文体积增大。
- 风险：迁移遗漏产生双份事实；由契约测试与关闭时三态梳理兜底。

## 取代

取代：无（细化并扩展 [文档规范](../standards/doc-governance.md) 治理机制）。
被取代：无。
来源范本：QED-Tracker（兄弟仓库，本工作区只读，路径以对方仓库为准，故不作可解析链接）
`QED-Tracker/docs/adr/0004-standards-governance-alignment.md`、
`QED-Tracker/docs/standards/doc-governance.md`。
