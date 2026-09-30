# ADR 0009：数据根必须显式配置（去仓内静默回退与留档区双身份）

状态：Accepted
最后更新：2026-09-29
关联代码：`src/axiom_flow/config.py`（`_default_data_dir`、`Settings.data_dir`、`load_settings`）、
`src/axiom_flow/ingest/artifacts.py`（`book_dir_for`/`version_dir_for` 的 `parsed/` 前缀与
`ensure_within`）、`src/axiom_flow/api/admin.py`（清除作用域限 `parsed/`）
关联测试：`tests/unit/test_config.py`、`tests/unit/test_orchestrator.py`（数据根顶层断言）、
`tests/contract/test_storage_conventions_governance.py`（本决定落地后新增）

## 背景

`QED_DATA_ROOT` 未配置时，`config.py:47-49` 的 `_default_data_dir()` 把数据根**静默回退**到仓库内
`data/`（并作为 `Settings.data_dir` 的默认值）。这条回退与仓内既有口径直接冲突，且与根仓库
ADR 0018 的顶层白名单制不相容：

1. **文档与代码互相打脸**：[operations.md](../guides/operations.md)「数据目录」节明写 `data/`
   「**不作数据根候选**，真实数据根唯一事实源是 `QED_DATA_ROOT`」，代码却恰恰把它当默认数据根。
2. **回退目标正是野生区最密集处**：仓内 `data/` 顶层实测含 `probes/`（真机窗口留档，含数百个
   JSON）、`backups/`（D 类备份留痕）、`books/`（v1 扁平产物布局残骸）与两个旧 runbook 的
   `.log`。若漏配环境变量而起服务，产物会落进留档区，而 `api/admin.py` 的清除作用域守卫只看
   「是否在数据根 `parsed/` 内」——同一份留档区会被当成可清除对象，fail-open。
3. **根侧规则已把守护下放子项目**：根仓库 ADR 0018 设立数据根顶层白名单（`raw/`、`tmp/`、
   `parsed/`、`backups/`），并在「执行与门禁」节明写「数据根落盘行为由子项目各自契约测试守护」。
   本仓既无白名单声明，也无从机械核对骨架。
4. **既有纪律要求 fail-closed**：[ADR 0004](0004-model-boundary-and-pipeline-ownership.md) 确立
   「无静默兜底」；`config.require_admin_configured()` 已按同一路径处理管理面凭据缺失（启用而
   无 token 即拒绝启动）。数据根是同等量级的边界配置，却走了相反的路。

## 决定

1. **数据根唯一事实源是 `QED_DATA_ROOT`**：`Settings.data_dir` 不再有仓内默认值；未配置（或解析后
   目录不存在/不可创建）时 `load_settings()` 在启动期抛配置错误并**拒绝起服**，错误文案直接给出
   应设置的变量名与推荐值。与 `require_admin_configured()` 同一 fail-closed 纪律。
2. **仓内 `data/` 永久不是数据根**（**本条的「留档与备份区」定性已被 [ADR 0012](0012-workspace-directory-hygiene.md) 取代**：仓内留档区已于 2026-09-29 退役、内容清空且不备份，窗口留档与临时件改落仓库根 `tmp/`；「仓内目录不得当数据根」及其代码守卫由 ADR 0012 决定 2 明确保留、继续有效）：其定性收敛为单一身份——**留档与备份区**（`data/probes/` 真机
   窗口证据、`data/backups/` D 类备份留痕）。它不在数据根之内，故根侧顶层白名单与「元数据 JSON 岛」
   规则不作用于它；反过来，它的**顶层**不得出现产物目录形状（`book.json`/`pages/`/`versions/`），
   由守护断言。唯一的合法例外是 `data/probes/<窗口>/root/`——那是窗口脚本**显式声明的探针数据根**，
   其内 `parsed/` 布局正当，与真实数据根同受「只写 `parsed/`」约束。历史残骸由本决定的实施轮清理登记。
3. **顶层白名单沿用根仓库 ADR 0018**，本仓不自行增删条目：`raw/`（只读输入）、`tmp/`（本服务
   不写）、`parsed/`（本服务唯一写入区）、`backups/`（D 类留痕例外区）。白名单以哨兵区
   （`<!-- data-root-whitelist:start -->` … `:end -->`）落在
   [overview.md](../architecture/overview.md)，供守护比对；白名单之外即野生区，发现即登记处置。
4. **本仓写入面锁在 `parsed/`**：产物路径一律经 `ingest/artifacts.py` 的两个构造器生成（自带
   `parsed/` 前缀 + 单段名校验 + `ensure_within`），清除作用域限 `parsed/` 子树；向数据根其他
   顶层区（含 `raw/`、`tmp/` 与白名单外目录）的写入路径判为缺陷。
5. **守护落地**：新增 `tests/contract/test_storage_conventions_governance.py`，断言哨兵区存在且
   与常量清单一致、虚构约定 `tmp/axiom-flow/` 在治理文档命中为 0、缺 `QED_DATA_ROOT` 必报错、
   产物路径构造器一律带 `parsed/` 前缀且 `ensure_within` 对越出**数据根**的写路径必报错、
   `data/` **顶层**不含产物形状（探针数据根 `data/probes/*/root/` 例外）。每条断言做变异复验。
6. **兼容**：现网 `.env` 已配置数据根，真机窗口脚本与探针根本就显式传根，故本决定只改变
   「漏配时」的行为——从静默写进仓内留档区变为拒绝启动。受影响需同步的断言与文档在实施轮一次改齐。

## 后果

- **正面**：数据根双身份消除，留档区不会被当成可清除对象；骨架可机械核对，白名单漂移由守护
  而非人工发现；配置缺失从「悄悄写错地方」变成启动期可见错误，符合 fail-closed 纪律。
- **代价**：任何未设 `QED_DATA_ROOT` 的裸启动（新脚本、临时环境、他人 clone）会立即失败；
  `tests/unit/test_config.py` 三处把回退钉成契约的断言必须改写；`overview.md`、`operations.md`、
  `parsing-pipeline.md` 三处文档需同步。
- **缓解**：错误文案给出变量名与推荐值，失败即可自助修复；`operations.md` 与
  `local-dev.md` 的配置表列出该变量为必填；探针/测试窗口继续用临时根或 `data/probes/<窗口>/root`
  显式传根，不受影响。多实例或多数据根场景不在本决定内，需要时先立取代 ADR。

## 取代

取代：无（本决定是新增边界约束，不改写 ADR 0004/0005 的既有裁决）。
被取代：决定 2 的「仓内 `data/` 留档与备份区」定性被 [ADR 0012](0012-workspace-directory-hygiene.md) 取代（2026-09-29，仓内留档区退役）；
决定 1/3/4/5 与本 ADR 其余约束仍生效。正文里的 `data/probes/…`、`data/backups/…` 随 ADR 0012 决定 6 一律读作
`tmp/probes/…`、`tmp/backups/…`（仓库根 `tmp/` 是仓内唯一临时与留档区）；「仓内路径不得当数据根」及其守卫不变。
关联：根仓库 ADR 0018（顶层白名单与 JSON 岛禁止，跨仓库只读引用、不建相对链接）；
[ADR 0004](0004-model-boundary-and-pipeline-ownership.md)（无静默兜底）；
[ADR 0008](0008-fixed-doc-form-and-metadata-genres.md)（固定文档骨架与可对账契约表）；
实施载体 [PLAN-016](../history/plans/2026-09/2026-09-24-storage-convention-alignment.md)（ARCH-034，已关闭），
入站确认包 PLAN-015（REQ-095，已按 `Delete` 关闭，判定与回执正文见提交 `a97ccc7` 与
[completed.md](../trackers/completed.md) 同批行）。
