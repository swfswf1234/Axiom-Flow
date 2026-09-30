# ADR 0012：工作区目录卫生（仓内 `tmp/` 唯一临时区、仓内留档区退役、无前端、`logs/` 受限）

状态：Accepted
最后更新：2026-09-29
关联代码：`.gitignore`（`/tmp/` 条目）、`scripts/axiom_flow_service.py`（`LOG_DIR`/`PID_FILE`/`SERVE_LOG`，
本决定不改其代码）、`src/axiom_flow/config.py`（`_resolve_data_dir` 仍拒仓内目录作数据根）
关联测试：`tests/contract/test_storage_conventions_governance.py`（本决定落地后新增的目录卫生断言）

## 背景

仓库根的目录用途长期靠各处散文描述，且与真实落盘状态分叉：

1. **留档区与数据根双身份的历史包袱**：[ADR 0009](0009-data-root-must-be-explicit.md) 决定 2 把仓内
   `data/` 定性为「**永久**留档与备份区」。实施后该区长到 2468 个文件 / 466 MiB（真机窗口证据 +
   3 份 `af_backup_*.sql`），既不入版本库（`.gitignore`）又无检索入口，实际作用是「只有本机知道的
   证据堆」，且每次 D 类窗口都往里追加，与「工作树干净、产物只落 `QED_DATA_ROOT`」的口径相斥。
2. **临时脚本落在入库目录里**：21 个真机窗口一次性脚本放在 `scripts/tmp/` 且**已入库**，
   被 `operations.md` runbook 与 `code-map.md` 按路径引用；目录名（`tmp`）与入库事实自相矛盾，
   `docs/trackers/todo.md` 的长期行 ARCH-010 把它列为遗留项。一次性脚本入库后无人清理、
   每次改动都要走门禁，而它们的价值只在当次窗口的取证。
3. **前端已移交但目录还在**：`web/`（`index.html`/`app.js`/`style.css`）仍在仓内且 tracked，
   `code-map.md` 已标「移交根仓库 QED-Engine（本仓库不再维护）」；v1 残骸 `app/` 只剩 `__pycache__`
   字节码，无 tracked 文件。
4. **`logs/` 无上限口径**：`axiom_flow_service.py` 把子进程 stdout/stderr 以 `"ab"` 追加到
   `logs/qed-axiom-serve.log`，无轮转；另有历史 feature 遗留的 `qed-axiom-mode`（全仓无读写者）。
5. **解析产物曾有的仓内回退**：已由 ADR 0009 决定 1 消除（缺 `QED_DATA_ROOT` 即启动期报错），
   本轮**不改写**该裁决，只处理留档区的定性。

## 决定

1. **仓库根 `tmp/` 是唯一的临时与留档区**：一次性窗口脚本、真机取证留档、D 类备份文件一律落
   `tmp/`，`.gitignore` 以 `/tmp/` 排除 ⇒ **不入库、不作为事实源、内容视为不重要、可随手清理**。
   需要长期保存的结论必须写进 `docs/`（台账/文档），而不是留在 `tmp/` 里；`tmp/` 内文件随时可能消失。
   与数据根 `<QED_DATA_ROOT>/tmp/`（根仓库 ADR 0018 白名单区，本服务不写，见 `PP-6`）是**两个不同位置**，
   文档与本 ADR 凡指仓内者一律写「仓库根 `tmp/`」。
2. **仓内 `data/` 留档与备份区退役**：不再存在、不再作为任何用途的落点，历史内容（窗口证据与
   备份 sql）按用户裁决直接清空且不备份。**取代** [ADR 0009](0009-data-root-must-be-explicit.md) 决定 2
   中「仓内 `data/` 是永久留档与备份区」的定性；ADR 0009 的决定 1/3/4/5（数据根必须显式配置、
   顶层白名单哨兵区、写入面锁 `parsed/`、守护落地）**继续有效**，「仓内目录不得当数据根」的
   断言与代码守卫原样保留。
3. **`scripts/` 只放长期受管的脚本**：当前唯一受管脚本是服务启停 `axiom_flow_service.py`；
   临时/一次性驱动与探针不得置于 `scripts/`（含任何子目录），一律仓库根 `tmp/`。
4. **`logs/` 只写本项目运行日志，文件数固定不超过 3 个**：区内只允许本项目自有前缀
   （`qed-axiom`，覆盖 `qed-axiom-serve.log` 与 `qed-axiom.pid`）的运行时文件，且总数 ≤3；超出即由人工删除最旧者。本决定**只成文口径**，
   不在服务脚本里引入轮转逻辑（用户裁决 2026-09-29：不改代码）。
5. **本仓库不含前端**：`web/` 已删除，UI 与对照渲染归 QED-Engine 根仓库；本仓库只提供 8902 API
   契约与产物格式。未 tracked 的 v1 残骸 `app/` 一并物理移除。
6. **守护落地**：`tests/contract/test_storage_conventions_governance.py` 新增目录卫生断言——
   仓内不存在 `data/`、不存在 `scripts/tmp/`、不存在 `web/`；`.gitignore` 含 `/tmp/`；
   `tmp/` 下无 tracked 文件；`logs/` 存在时文件数 ≤3 且全部为 `qed-axiom` 前缀。
   每条断言实施时做变异转红复验。历史归档文档（`docs/history/`）与台账当时记录中的旧路径
   按用户裁决**改指新落点**（`scripts/tmp/…` ⇒ `tmp/…`、`data/probes/…` ⇒ `tmp/probes/…`），
   其内容已不存在，路径只表示当时的落点位置。

## 后果

- **正面**：工作树不再累积无人清理的证据堆；一次性脚本脱离门禁与 diff 噪声；「哪些目录能放什么」
  成为一条可机械核对的口径，漂移由守护发现而非人工巡检；前端边界与实际代码位置一致。
- **代价**：`tmp/` 不入库 ⇒ 真机窗口的原始证据**不再可复查**，历史文档里改指的 `tmp/probes/…`
  路径指向的是已被清空的位置；一次性脚本失传后需按 runbook 文字重建。ADR 0009 决定 2 的原文
  与本轮之后的仓内事实不再一致，须靠取代注记读取。
- **缓解**：窗口的**结论**（读数、判据、指纹）历来即写入 `docs/trackers/` 与计划壳，事实源不在
  `tmp/`；`ops` 类窗口若要保留回滚锚点，须按 ADR 0009 决定 3 写进数据根 `backups/`（白名单区），
  而不是仓内；本 ADR 决定 6 的断言防止残骸复积。

## 取代

取代：[ADR 0009](0009-data-root-must-be-explicit.md) **决定 2**（仓内 `data/` 永久留档与备份区的定性）；
ADR 0009 其余决定与本 ADR 并存生效。
被取代：无。
关联：[ADR 0004](0004-model-boundary-and-pipeline-ownership.md)（无静默兜底）、
[ADR 0009](0009-data-root-must-be-explicit.md)（数据根显式配置与顶层白名单）、
根仓库 ADR 0018（数据根顶层白名单，跨仓库只读参照）、
实施载体 [PLAN-021](../history/plans/2026-09/2026-09-29-workspace-directory-hygiene.md)（已按 Retain 归档）。
