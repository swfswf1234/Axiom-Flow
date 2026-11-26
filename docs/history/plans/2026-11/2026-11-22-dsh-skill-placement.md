# DSH 治理技能落位（本地技能正本入 rank 100）

状态：Completed
任务类型：B
最后更新：2026-11-26
关联 ADR：[ADR 0012](../../../adr/0012-workspace-directory-hygiene.md)（工作区目录卫生：新增受管目录须登记）、
[ADR 0003](../../../adr/0003-standards-governance-alignment.md)（治理模式对齐）
关联设计：无（技能正本是过程载体，不是设计契约；语义事实源在 `docs/standards/`）
关联 Tracker：docs/trackers/completed.md 的 PLAN-044／REQ-125 关闭行（技能治理线）
归档判定：Retain（受控差异对照表是不可再生的判据证据）

## 目标与成功标准

**目标**：让 Axiom-Flow 会话内的 DSH 技能发现链路接通——把 QED 治理技能按「同骨架、受控差异」
落到本仓库 `.dsh/skills/`（DSH 扫描 rank 100），使技能正本可被版本控制、可被 DSH 真实加载。
**2026-11-25 补充**：原六枚外新增第七枚 `qed-verification`，把 `qed-implement`／`qed-closeout` 中三处
对本仓不可达的通用技能裸调用就地承载；三条通用技能仍不整体引入（维持非目标）。

**成功标准**：

- S1 `.dsh/skills/<name>/SKILL.md` 目录形态成立，技能名集合＝根仓库治理四件套＋两枚通用工程技能
  ＋本仓新增 `qed-verification`（共七枚）。
- S2 每篇 frontmatter 合法（`description` 含 `: ` 时加双引号），DSH 侧不再静默丢弃。
- S3 「受控差异」逐条可对账：路径、ADR 编号、命令入口三项按本仓库事实改写，其余骨架不动。
- S4 技能只引用「概念」，实际文件由 `AGENTS.md`「标准映射」定位，不硬编码根仓库路径。
- S5 `.gitignore` 以内容模式白名单纳入 `.dsh/skills/`，其余 DSH 本机面继续忽略。
- S6 文档落位：`local-dev.md` 的扫描根表与 `development.md` 的标准映射机制段同步改判。

## 范围与非目标

**范围**：`.dsh/skills/` 下四枚治理技能（`qed-intake`／`qed-plan`／`qed-implement`／`qed-closeout`）
+ 两枚通用工程技能（`cjk-safe-edit-and-verify`／`dsh-tooling-conventions`）的正本落位，
+ 2026-11-25 新增第七枚 `qed-verification`（承载三处通用纪律、修复裸调用）；`.gitignore` 白名单；
`docs/standards/local-dev.md` 与 `docs/guides/development.md` 两处文档同步。

**非目标**：

- 不改根仓库 `.dsh/skills/`（只读参照），不建镜像同步脚本——两端各自演进，靠「同骨架」约束而非复制。
- 不引入 `systematic-debugging`／`test-driven-development`／`verification-before-completion` 三枚通用技能
  （它们属工具层通用能力，与本仓库治理无关；需要时由用户另行裁决）。
- 不改 `.qoder/skills/`（Qoder 端正本，另一条通道）。
- 不把技能正文变成设计契约：技能只写「步骤 + 指针」，判据正文仍在 `docs/standards/`。

## 前置条件

- [ADR 0012](../../../adr/0012-workspace-directory-hygiene.md) 决定 1 已定「仓库根 `tmp/` 是唯一临时区」；
  新增 `.dsh/skills/` 是**受管目录**而非临时区，须在 `overview.md` 目录卫生节与本计划同时登记，
  否则与「目录卫生」口径冲突。
- 用户 2026-11-22 已裁「选甲」：在本仓库建 `.dsh/skills/`、落 rank 100，不选用户级 `~/.dsh/skills`。
- DSH 扫描根规则已实测确认：项目根＝最近含 `.git` 祖先，Axiom-Flow 自身即为项目根（证据见
  [local-dev.md「DSH 技能正本与扫描根」](../../../standards/local-dev.md)）。

## 工作项

| 编号 | 工作项 | 落点 | 状态 |
| --- | --- | --- | --- |
| W1 | 建 `.dsh/skills/` 六个技能目录与 `SKILL.md` 正本（2026-11-25 再加第七枚 `qed-verification`） | `.dsh/skills/*` | 已完成（六枚 2026-11-22 落位，第七枚 2026-11-25 落位；每目录恰一份 `SKILL.md`） |
| W2 | 受控差异改写：路径／ADR 编号／命令入口按本仓库事实；三处通用技能裸调用改指 `qed-verification` | 各 `SKILL.md` | 已完成（逐篇对账表见下「受控差异对账」节；`qed-implement` 两处／`qed-closeout` 一处裸调用已清） |
| W3 | `.gitignore` 白名单：`.dsh/*` + `!.dsh/skills/` | `.gitignore` | 已完成（`:25-28`，两条已在册） |
| W4 | 文档同步：扫描根表改判、标准映射机制段改判、目录卫生登记 | `local-dev.md`、`development.md`、`overview.md` | 已完成（`local-dev.md:175-210` 扫描根表与七枚对账表、`development.md:103-121` 标准映射机制段；`overview.md` 目录卫生节此前已登记 `.dsh/skills/` 受管目录） |
| W5 | 验证：技能可被 DSH 发现 + 契约与全量门禁 | — | 已完成（新技能出现在会话技能目录 rank 100；frontmatter 只读校验七枚全合法；全量门禁绿，读数见「过程与证据」） |

## 受控差异对账（V3）

| 技能 | 相对根仓库同名的受控差异 | 类别 |
| --- | --- | --- |
| `qed-intake` | 作用域收缩到本仓；ADR 编号改本仓同义（0006/0011/0013/0018）；台账列名按本仓七列 | 作用域＋编号 |
| `qed-plan` | 同上；新增「ADR 编号只在本仓库内解释」条 | 作用域＋编号 |
| `qed-implement` | 同上；隔离铁律补 `QED_TEST_MYSQL` 例外与工作区边界；先红／根因两处调用改指 `qed-verification` | 作用域＋编号＋命令入口 |
| `qed-closeout` | 门禁命令改本仓两条；补六步收口顺序与 `develop` 分支纪律（ADR 0014）；完成前验证调用改指 `qed-verification` | 编号＋命令入口 |
| `dsh-tooling-conventions` | 完整性边界、缓存目录、扫描根、脚本位置全按本仓实测改写 | 本机实测事实 |
| `cjk-safe-edit-and-verify` | 探针示例锚改本仓实体（`KA-22`、`/kp-jobs`）；补 DSH 缓存目录回避 | 本机实测事实 |
| `qed-verification` | 本仓新增（根仓库无同名），三条纪律按本仓 `testing.md`／`development.md` 指针就地成文 | 新增（非受控差异项） |

## 过程与证据

- 2026-11-22｜六枚技能落位 `.dsh/skills/`，`.gitignore` 两行白名单与两处文档改判完成，DSH rank 100 命中。
- 2026-11-25｜审计全局 `~/.dsh/skills`（仅一枚机器级 `dsh-tooling-conventions`）与本仓六枚：发现
  `qed-implement`／`qed-closeout` 三处裸调用指向本仓不可达的三枚通用技能。按用户裁决新增第七枚
  `qed-verification`（先红 → 根因 → 完成前证据，判据指针归 `testing.md`／`development.md`），三处裸调用
  改指它；三条通用技能仍不整体引入（维持非目标）。新增技能随即出现在会话技能目录（DSH watch 热更新）。
- 2026-11-25｜只读校验：本仓七枚 `SKILL.md` 目录形态齐备、frontmatter YAML 合法、`name` 与目录名一致；
  两枚继承描述超长（215／238 字符）经用户裁决接受，不属硬门禁。全量门禁 `pytest tests -q` +
  `ruff check src tests` 绿（读数见 `project-status.md`／`completed.md` 本次收尾行）。

## 验证与验收

- **V1 结构**：`.dsh/skills/` 七枚目录齐备，每目录恰一份 `SKILL.md`（2026-11-25 读数见「过程与证据」）。
- **V2 frontmatter**：遍历所有 `SKILL.md`，`name` 与目录名逐字相等、`description` 非空；
  含 `: ` 者加双引号（YAML 可解析）。
- **V3 受控差异对账**：逐篇比对根仓库同名技能，差异仅限本计划「受控差异」表所列三条，
  其余段落逐字一致或按本仓库事实等价改写；差异表在 W2 完成时给出。
- **V4 概念引用**：`SKILL.md` 内不出现根仓库专属路径（`QED-Tracker`、`web-ui/`、`docs/plans/index.md`
  之外的本仓不存在的路径），且不出现根仓库 ADR 编号误用（本仓 ADR 编号与根仓库不同义）。
- **V5 DSH 真实加载**：新会话技能目录中出现七枚技能（rank 100 命中；`qed-verification` 新增后本会话目录即热更新出现）。
- **V6 门禁**：`pytest tests -q` + `ruff check src tests` 全绿；`tests/contract` 全绿。

## 回滚

删除 `.dsh/skills/` 整目录、回退 `.gitignore` 三行与两处文档改判即可；`tmp/acl-report/` 的权限修复
与本计划无关，不受回滚影响。技能是纯文档载体，无数据面、无迁移、无运行时副作用。

## 关闭与归档

六个工作项全绿后按 [ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md) 六步收口：
本壳写结论 → todo 行（REQ-125）压缩关闭 → 迁移 `completed.md` → `plans/index.md` 登记 Retain →
`project-status.md` 刷新 → 全量门禁。本壳按 **Retain** 归档至 `docs/history/plans/2026-11/`
（受控差异对照表是「为什么这样改写」的唯一推导依据，不可再生）。

## PLAN-044 收口结论（2026-11-26）

**关闭结果：`Achieved`。** 成功标准 S1~S6 逐条达成：

| 判据 | 达成证据 |
| --- | --- |
| S1 七枚技能目录形态 | `.dsh/skills/` 下 `qed-intake`／`qed-plan`／`qed-implement`／`qed-closeout`／`cjk-safe-edit-and-verify`／`dsh-tooling-conventions`／`qed-verification` 七目录，各一份 `SKILL.md`；只读校验 COUNT=7 |
| S2 frontmatter 合法 | 七枚 YAML 可解析、`name` 与目录名逐字相等、`description` 非空；含 `: ` 者均加双引号；本会话技能目录七枚全部可见（DSH watch 热更新） |
| S3 受控差异可对账 | 本壳「受控差异对账」表逐篇给出七枚差异类别；三处通用技能裸调用已改指 `qed-verification`，零悬空调用 |
| S4 概念引用 | 除「工作区边界」条点名 QED-Engine 根仓库／QED-Tracker 只读外，技能正文只引用概念，实际文件由 `AGENTS.md` 标准映射定位 |
| S5 `.gitignore` 白名单 | `.gitignore:25-28` `.dsh/*` + `!.dsh/skills/` 在册；其余 DSH 本机面继续忽略 |
| S6 文档落位 | `local-dev.md:175-210` 扫描根表与七枚对账表、`development.md:103-121` 标准映射机制段同步；两处描述超长经用户裁决接受 |

**REQ-125（长期行）一并关闭**：其退出条件（`.qoder/skills/` 四件套与 `settings.json` 入库、
`qed-intake` 去端专有工具名且描述 ≤200、骨架与根仓库对齐）经只读核对成立——
`git ls-files .qoder` 命中五文件、`.qoder/skills/qed-intake/SKILL.md:3` 描述无端专有工具名且 191 字符。
两行同批迁入 `completed.md`。**归档判定 `Retain`**：受控差异对账表与三处裸调用改指的推导不可再生，
壳迁 `docs/history/plans/2026-11/`。全量门禁读数见 `completed.md` 本行。
