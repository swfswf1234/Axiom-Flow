# 解析产物可信与增量重跑实施（parse-artifact-trust）

状态：Completed
任务类型：B
最后更新：2026-11-23
关联 ADR：[ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0007](../../../adr/0007-parse-status-semantics.md)（`completed` 与激活同一判据；其「产物在位」子句按本次修订继续生效）、[ADR 0015](../../../adr/0015-db-single-source-for-readiness.md)（**2026-11-23 就地修订**：DB 管状态、文件系统做完整性核验，状态确认后核验文件合理且必要，不替代状态源）、[ADR 0009](../../../adr/0009-data-root-must-be-explicit.md)（数据根 fail-closed，清点只读不自动创建）、[ADR 0004](../../../adr/0004-model-boundary-and-pipeline-ownership.md)（模型版本探针归 8900 侧，P1 只消费 `/health` 读数）、[ADR 0016](../../../adr/0016-af-family-rebuild-into-four-tables.md)（承载随步：`af_parse_jobs` 折叠为 `af_operations` 的 `op_kind='parse'` 事件行，语义不变）
关联设计：[parsing-pipeline.md](../../../design/parsing-pipeline.md)、[parsing-management.md](../../../design/parsing-management.md)、[database-design.md](../../../architecture/database-design.md)、[api.md](../../../architecture/api.md)
关联 Tracker：docs/trackers/todo.md（PLAN-019 镜像行；承载 ARCH-022；REQ-123 已并案了结，见文末历史节）
归档判定：Retain（含 P 系列契约草案、逐条判据沿革与 2026-11-23 目标重定义，关闭时归档）

> **2026-11-23 立项返工重定（用户裁决，本壳口径的新上界）**：本壳建立时未深思熟虑，原 S1「双全等⇒默认不重跑」、
> C2「409 + force 门」、原 S3/W5「去掉 `is_file()`、DB-only」与人的设计不符，全部**作废**。新设计：
> **① DB 控制一切状态、文件系统保存内容，确认 DB 状态后核验文件真实状态合理且必要（完整性核验，不是第二状态源）；
> ② 重跑与否、范围由人决定，系统只给建议不替人决定；③ 默认补未完成部分（人可显式选全本或已完成页）。**
> ADR 0015 同日就地修订为「DB 管状态、文件核验合法」。本文历史节（W1 勘察、W2 落地、REQ-123 回执、
> 评审裁决 R1~R8、口径台账）保留作沿革，凡与新设计冲突的措辞一律以本节与新成功标准为准。

## 目标与成功标准

**目标**：让「看状态 → 人决定 → 按范围重跑」的链路清楚：人从逐页状态（DB）与产物在位（文件核验）
看清现状，系统给建议不替人决定；人选择部分或全部页后，系统按范围重跑，默认可补未完成部分。

成功标准（2026-11-23 重定，取代原 S1~S6）：

| ID | 标准 |
| --- | --- |
| S1 | `POST /parse-jobs` 支持 `scope + pages`：`pages` 显式即范围，`scope` 可表达全本/未完成/失败页；缺省行为保持向后兼容（`pages` 与 `scope` 都缺省 ⇒ 全本） |
| S2 | 系统提供只读建议清单：逐页给出 `status`（DB）、`has_artifact`（文件核验）、`suggested`/`reason`（未完成/失败/文件缺失/签名仅建议），不替人决定、不设 409/force 门 |
| S3 | 人提交重跑后，受理时对当前可见版本的目标页把 `af_pages.status` 写 `failed` 并写明理由，新任务不继承这些页旧产物、重跑后产物在新版本内覆写生效；文件缺失但 DB=parsed 的页走同一路径 |
| S4 | **移出本壳（2026-11-23 用户裁决）**：未知字面值兜底 ledger 与重跑范围主线弱耦合，落点属检索/知识库线，移入 ARCH-010 长线；本壳不判 |
| S5 | **移出本壳（2026-11-23 用户裁决）**：过时/残骸只读清点与清理属 D 类运维线，移入 ARCH-010 长线；本壳不判 |
| S6 | 页确认门（原 S5）已随 PLAN-018 关闭；本壳不再承载其判据，Q9 随 PLAN-018 归档自然了结 |

## 范围与非目标

- 范围：`POST /parse-jobs` 的 `scope` 参数与受理时的目标页状态复位；只读建议清单端点；签名降级为建议信号；
  `normalize/` 分诊 ledger（S4）；过时只读清点（S5）；`api.md`/设计/code-map 同步与契约测试。
- 非目标：系统自动决定重跑与否（人的职责）；自动删除任何历史产物（D 类另批）；知识库 R 系列（ARCH-023）；
  8903 前端按钮改造（根仓库自为）；区域驱动解析本体（PLAN-018 已关闭）；改引擎与模型部署（ADR 0004）。

## 前置条件

1. ADR 0015 已于 2026-11-23 就地修订：DB 管状态、文件核验为完整性检查，两者职责分离。
2. 现有 `GET /books/{id}/pages` 已给逐页 `status` + `has_artifact`，`failed_pages` 给失败页集合，
   是建议清单的现成数据源；`PageRepository.upsert` 可写 `status=failed/error`。
3. 现有 `seed_version_from`（硬链接写时复制）+ `_write_page_artifacts`（新版本目录覆写）保证
   「新版本重跑目标页不破坏旧版本」，S3 的实现依托此机制而非另造原地覆盖。
4. 签名模块 `orchestrator/signature.py` 的纯函数与单测已就位，本轮只改用途（建议信号），不改输入集。
5. 真机面：业务库 `qed` 保持空，`QED_TEST_MYSQL=1` 层禁跑（夹具 teardown 清演示数据）；本壳终验不依赖真机，
   只读建议与 scope 执行由本地单测/契约测试取证。

## 契约草案（2026-11-23 新设计）

### C1 · `POST /api/v1/parse-jobs` 的 scope（向后兼容）

请求体新增 `scope: Literal["all","unfinished","failed","selected"] | None = None`；
`pages` 保持 `list[int] | None`，语义：
- `pages` 显式（`selected`）⇒ 按 `pages` 执行（可重跑已完成页）；
- `scope=all` ⇒ 全书（忽略或不填 `pages`）；
- `scope=unfinished` ⇒ 未完成集合 = `status != parsed` ∪ (`parsed` 且 `has_artifact=false`)，理由分别 `not_parsed`/`artifact_missing`；
- `scope=failed` ⇒ 当前可见版本 `status=failed` 或任务 `failed_pages` 中的页；
- 都缺省 ⇒ **全本**（现状行为，向后兼容 8903）。

受理顺序不变（书目 → ingest → 页码越界 → 框数上限 → 引擎 → 探活）；签名不进受理门。受理通过后、建任务前：
对当前可见版本的本次目标页写 `af_pages.status=failed, error="rerun requested: scope=... reason=..."`（原子提交失败则整次受理失败，
不留半成品）。随后建新任务，后台执行不继承目标页旧产物。

### C2 · 只读建议清单

`GET /api/v1/books/{book_id}/reparse-plan`：返回逐页 `{page_no, status, has_artifact, suggested, reason}`；
`reason ∈ {failed, not_parsed, artifact_missing, signature_changed, signature_unknown}`（签名类仅建议，不阻断任何提交）。
纯函数 `unfinished_pages()` 供清单与 scope=unfinished 共用，禁止两处各写一遍。

### C3 · 签名的用途降级

`compute_signature`/`decide_rerun` 保留为纯函数与单测，仅用于清单的 `signature_*` 建议列；
**删除原 C2 的 409/force 门与原 S1「双全等默认不重跑」**——任何情况下人不被系统阻止提交重跑。

## 过程与证据

- **2026-11-23 立项返工批（W0~W3 实施，门禁全绿）**：
  - W0：ADR 0015 就地修订（DB 管状态、文件核验合法，撤销原 `is_file()` 去除决定）；本壳目标/S1~S6/
    草案 C1~C3/工作项按新链路重写；todo 的 PLAN-019/ARCH-022 两行成功标准按 ADR 0013 立项返工重定。
  - W1 先红：`tests/contract/test_reparse_scope.py` 4 例（scope 闭集/建议清单/受理置 failed/显式重跑已完成页）
    与 `tests/unit/test_reparse_artifacts.py` 1 例（硬链接覆写不破坏旧版本）先红（AttributeError/404/status=parsed）。
  - W2 实现：`schemas.py` 加 `ParseJobCreate.scope` + `ReparsePlanOut/ReparsePageAdvice`；新模块
    `orchestrator/reparse.py`（`build_reparse_plan` 逐页建议 + `resolve_scope` 纯函数）；`api/main.py`
    加 `GET …/reparse-plan` 与 `create_parse_job` 的 scope 解析/受理置 failed（scope 显式时把当前可见
    版本目标页写 `failed/error=理由`，新任务不继承该页），scope 缺省全本向后兼容。
  - W3 同步：`api.md` 端点总表 + 两小节、`code-map.md` 两行、全量门禁
    `pytest tests -q` **1734 passed / 18 skipped** + `ruff check src tests` All checks passed。
  - 相关既有套件回归 `test_api_v1_contract.py`/`test_schemas.py`/`test_orchestrator.py` **359 passed**。
  - 本批未提交、未推送、未打标签；未触业务库与真实数据根。
- **2026-11-23 归属裁决＝S4/S5 移出本壳（用户裁定，不阻塞收尾）**：S4「未知字面值兜底 ledger」与 S5
  「过时/残骸只读清点」与「人决定重跑范围」主线弱耦合，且分诊 ledger 的落点（分块规则）属检索/知识库线、
  清点清理属 D 类运维线；两项移入 [ARCH-010](../../../trackers/todo.md) 长线「文档与代码对账维护」的待整理项
  （长期行承载，成功标准不变），本壳 S4/S5 判 **Not Applicable（移出）**，不计入本壳关闭判据。
  本壳成功标准收敛为 S1/S2/S3 + S6（S6＝确认门随 PLAN-018 已关闭，自然了结）。
- **2026-11-23 模拟验收（隔离环境，不触真实数据根/业务库）**：以 `tests/contract/test_reparse_scope.py`
  与 `tests/unit/test_reparse_artifacts.py` 的假引擎 + 临时数据根跑通四个场景——① `scope=unfinished`
  对「parsed 但 blocks.json 缺失」页：建议清单标 `artifact_missing`，受理后当前版本该页 `status=failed`
  且 error 带理由，新任务重跑后该页在新版本 `parsed` 且产物在位；② `scope=selected + pages=[1]` 可重跑
  已完成页；③ scope/pages 都缺省仍全本（向后兼容）；④ 新版本覆写目标页不污染旧版本产物（原子替换打破硬链接）。
  5 例全绿；门禁全量 **1734 passed / 18 skipped** + ruff clean。

## 工作项

| # | 工作项 | 状态 |
| --- | --- | --- |
| W0 | 立项返工：ADR 0015 就地修订 + 本壳重定 + todo 成功标准重定 | 完成 2026-11-23 |
| W1 | 失败测试：scope 解析、未完成集合、受理置 failed、产物覆写不破坏旧版本 | 完成 2026-11-23（先红后绿 5 例） |
| W2 | 实现 scope + 建议清单 + 受理状态复位 + 新版本不继承目标页 | 完成 2026-11-23 |
| W3 | 文档同步（api.md / design / code-map）+ 全量门禁 | 完成 2026-11-23（1734 passed / ruff clean） |

## 验证与验收

- 契约：`POST /parse-jobs` 四种 scope 形状、`have pages` 显式范围、缺省全本兼容、签名不阻断。
- 单测：`unfinished_pages` 纯函数四理由；受理时当前版本目标页置 failed 且 error 有理由；
  文件缺失但 DB=parsed 的页进建议集合且不继承；新版本覆写目标页后旧版本同名文件仍在。
- 回归：全量 `pytest tests -q` + `ruff check src tests` 全绿。
- 不要求真机：真机读数按前置 5 不属本轮。

## 回滚

- `scope` 为可选字段、缺省全本 ⇒ 回滚即停用新 scope 分支与建议端点，现有 `pages` 提交路径零变化。
- 受理置 failed 只改当前可见版本的目标页状态、不删文件；回滚即恢复旧 handler，已有 failed 行可人工重判。

## 关闭与归档

- 关闭条件：**已达成（2026-11-23）**——S1/S2/S3/S6 逐条达成（S4/S5 经用户裁决移出转长线、不阻塞；
  S6 随 PLAN-018 关闭自然了结），隔离环境模拟验收四场景全绿、全量门禁 1734 passed / 18 skipped + ruff clean。
  六步收口（结论入本壳 → todo 两行关闭 → completed.md 两行 → plans/index 登记 Retain → project-status 刷新 → 门禁全绿）
  同批执行完毕。归档判定 **Retain**：壳归档至 `docs/history/plans/2026-09/`（含 2026-11-23
  目标重定义与历史沿革，作根侧沿革与后续 ledger/清点移出事项的索引）。

---

## 历史沿革（2026-11-23 前，按当时记录保留、不回改）

### 登记沿革与承载随步

本壳承接 PLAN-009「ARCH-022 · 解析面优化（P 系列）」的 P1/P2/P3（P4 暂缓、P5 无动作）。
2026-10-22 ADR 0016 档 B 把 `af_parse_jobs` 折叠为 `af_operations` 的 `op_kind='parse'` 事件行，
`parser_signature` 为其列；迁移链重置为唯一 base `20261022_0001`，历史 revision 号属当时执行记录。

### 原 W1 勘察登记（`/health` 取证，签名输入集冻结）

2026-09-28 留档 `tmp/probes/mineru-3.4.4-20260920/raw/001_health_200.bin`（257 B，sha256
`e8890befd049e0cd36a7c641d52047cca332716fda251f0823707795a3d81f7d`）实回 11 字段；
`version=3.4.4`、`protocol_version=2` 入签名，负载计数/窗口/超时不入。残余盲区＝同 version 换后端不可见，
建议 `backend` 记入 `progress`、清单以 `model_drift_suspect` 报警不自动判过时。
策略版本拆域＝`block_policy_version`（`_TYPE_MAP` + `_OUTPUT_FLAGS`，入签名）与 `chunk_policy_version`
（分块跳过集，不入签名）。冻结四元组与 canonical 串单测见 `tests/unit/test_parser_signature.py`。

### 原 W2 落地结论（签名写入面）

`parser_signature` 加列 + `orchestrator/signature.py`（canonical、哨兵 `''` 三边界、`decide_rerun`）+
`ParseJobRepository.find_in_flight` 已落地；当时门禁 624 passed / 10 skipped + ruff clean +
`QED_TEST_MYSQL=1` 8 passed，三处守护变异复验转红。偏差 D1~D7 与业务库 2026-10-03 窗口读数当时入册，
按沿革保留。受理门/清单端点当时未挂载；2026-11-02 实况登记签名零调用者、每行恒 `''`——本轮 W2 起
只把它用作建议信号，不接受理门。

### 承载 REQ-123（2026-10-23 已并案了结）

REQ-123 R1（ingest 复位 `last_parse_result`）经裁决「关闭且不记欠账」：ingest 不是任务，不回置结局列；
R2（两库三列对账）随 PLAN-024 档 B 整族重建消失，归一口径以单一事实源落 `database-design.md`/ADR 0015。
回执正文当时成文于本壳，todo 行已迁 `completed.md`。

### 原评审裁决（按当时记录保留，与新设计冲突处以 2026-11-23 重定为准）

- 2026-09-28 R1~R4：正本作用域（生效版本 + 在飞留存、图像产物物理例外）、确认门三形态（已移 PLAN-018）、
  ledger 落 `af_pages.quality`、签名含模型版本（先取证后动工）。
- 2026-10-01 R5~R8：原拟升级 DB-only 并去 `is_file()`（**2026-11-23 撤销**）、兜底 `PARAGRAPH`、
  图块维持跳过、ledger 只落库不回包。
- 口径台账 Q4（`AXIOM_PARSE_WINDOW_SIZE` 不入签名）、Q6-a（`PARAGRAPH`）、Q11（backend 报警）、
  Q12（在飞 409，**2026-11-23 作废**）按原记录保留；Q9（页确认态衔接）随 PLAN-018 归档自然了结。
