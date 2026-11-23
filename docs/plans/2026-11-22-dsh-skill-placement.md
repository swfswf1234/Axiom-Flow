# DSH 治理技能落位（本地技能正本入 rank 100）

状态：Accepted
任务类型：B
最后更新：2026-11-22
关联 ADR：[ADR 0012](../adr/0012-workspace-directory-hygiene.md)（工作区目录卫生：新增受管目录须登记）、
[ADR 0003](../adr/0003-standards-governance-alignment.md)（治理模式对齐）
关联设计：无（技能正本是过程载体，不是设计契约；语义事实源在 `docs/standards/`）
关联 Tracker：docs/trackers/todo.md 的 PLAN-044（并入长期行 REQ-125 的技能治理线）
归档判定：Retain（受控差异对照表是不可再生的判据证据）

## 目标与成功标准

**目标**：让 Axiom-Flow 会话内的 DSH 技能发现链路接通——把 QED 治理技能按「同骨架、受控差异」
落到本仓库 `.dsh/skills/`（DSH 扫描 rank 100），使技能正本可被版本控制、可被 DSH 真实加载。

**成功标准**：

- S1 `.dsh/skills/<name>/SKILL.md` 目录形态成立，技能名集合与根仓库治理四件套一致。
- S2 每篇 frontmatter 合法（`description` 含 `: ` 时加双引号），DSH 侧不再静默丢弃。
- S3 「受控差异」逐条可对账：路径、ADR 编号、命令入口三项按本仓库事实改写，其余骨架不动。
- S4 技能只引用「概念」，实际文件由 `AGENTS.md`「标准映射」定位，不硬编码根仓库路径。
- S5 `.gitignore` 以内容模式白名单纳入 `.dsh/skills/`，其余 DSH 本机面继续忽略。
- S6 文档落位：`local-dev.md` 的扫描根表与 `development.md` 的标准映射机制段同步改判。

## 范围与非目标

**范围**：`.dsh/skills/` 下四枚治理技能（`qed-intake`／`qed-plan`／`qed-implement`／`qed-closeout`）
+ 两枚通用工程技能（`cjk-safe-edit-and-verify`／`dsh-tooling-conventions`）的正本落位；
`.gitignore` 白名单；`docs/standards/local-dev.md` 与 `docs/guides/development.md` 两处文档同步。

**非目标**：

- 不改根仓库 `.dsh/skills/`（只读参照），不建镜像同步脚本——两端各自演进，靠「同骨架」约束而非复制。
- 不引入 `systematic-debugging`／`test-driven-development`／`verification-before-completion` 三枚通用技能
  （它们属工具层通用能力，与本仓库治理无关；需要时由用户另行裁决）。
- 不改 `.qoder/skills/`（Qoder 端正本，另一条通道）。
- 不把技能正文变成设计契约：技能只写「步骤 + 指针」，判据正文仍在 `docs/standards/`。

## 前置条件

- [ADR 0012](../adr/0012-workspace-directory-hygiene.md) 决定 1 已定「仓库根 `tmp/` 是唯一临时区」；
  新增 `.dsh/skills/` 是**受管目录**而非临时区，须在 `overview.md` 目录卫生节与本计划同时登记，
  否则与「目录卫生」口径冲突。
- 用户 2026-11-22 已裁「选甲」：在本仓库建 `.dsh/skills/`、落 rank 100，不选用户级 `~/.dsh/skills`。
- DSH 扫描根规则已实测确认：项目根＝最近含 `.git` 祖先，Axiom-Flow 自身即为项目根（证据见
  [local-dev.md「DSH 技能正本与扫描根」](../standards/local-dev.md)）。

## 工作项

| 编号 | 工作项 | 落点 | 状态 |
| --- | --- | --- | --- |
| W1 | 建 `.dsh/skills/` 六个技能目录与 `SKILL.md` 正本 | `.dsh/skills/*` | 待开始 |
| W2 | 受控差异改写：路径／ADR 编号／命令入口按本仓库事实 | 各 `SKILL.md` | 待开始 |
| W3 | `.gitignore` 白名单：`.dsh/*` + `!.dsh/skills/` | `.gitignore` | 待开始 |
| W4 | 文档同步：扫描根表改判、标准映射机制段改判、目录卫生登记 | `local-dev.md`、`development.md`、`overview.md` | 待开始 |
| W5 | 验证：技能可被 DSH 发现 + 契约与全量门禁 | — | 待开始 |

## 验证与验收

- **V1 结构**：`dir .dsh\skills` 六个目录齐备，每目录恰一份 `SKILL.md`。
- **V2 frontmatter**：遍历所有 `SKILL.md`，`name` 与目录名逐字相等、`description` 非空；
  含 `: ` 者加双引号（YAML 可解析）。
- **V3 受控差异对账**：逐篇比对根仓库同名技能，差异仅限本计划「受控差异」表所列三条，
  其余段落逐字一致或按本仓库事实等价改写；差异表在 W2 完成时给出。
- **V4 概念引用**：`SKILL.md` 内不出现根仓库专属路径（`QED-Tracker`、`web-ui/`、`docs/plans/index.md`
  之外的本仓不存在的路径），且不出现根仓库 ADR 编号误用（本仓 ADR 编号与根仓库不同义）。
- **V5 DSH 真实加载**：新会话技能目录中出现六枚技能（rank 100 命中）。
- **V6 门禁**：`pytest tests -q` + `ruff check src tests` 全绿；`tests/contract` 全绿。

## 回滚

删除 `.dsh/skills/` 整目录、回退 `.gitignore` 三行与两处文档改判即可；`tmp/acl-report/` 的权限修复
与本计划无关，不受回滚影响。技能是纯文档载体，无数据面、无迁移、无运行时副作用。

## 关闭与归档

六个工作项全绿后按 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md) 六步收口：
本壳写结论 → todo 行（REQ-125）压缩关闭 → 迁移 `completed.md` → `plans/index.md` 登记 Retain →
`project-status.md` 刷新 → 全量门禁。本壳按 **Retain** 归档至 `docs/history/plans/2026-11/`
（受控差异对照表是「为什么这样改写」的唯一推导依据，不可再生）。
