# 计划索引

状态：Current
最后更新：2026-09-29

本目录保存已批准且尚未关闭的执行计划。活跃计划的镜像行见
[待做任务](../trackers/todo.md)，计划正文是范围、验证和回滚的事实源。

## 关闭登记

- PLAN-001 文档清理与任务台账重构（Axiom-Flow）已完成（2026-09-11），计划按 `Delete` 删除；
  事实并入 tracker 与相关文档。
- V2-017 文档治理体系对齐（Axiom-Flow）已完成（2026-09-11），计划按 `Delete` 删除；
  事实并入 `docs/standards/`、`AGENTS.md`、`docs/guides/development.md`。
- PLAN-005 台账与计划承载契约治理（Axiom-Flow）已完成（2026-09-21），计划按 `Delete` 删除；
  事实并入 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)、
  [任务生命周期](../standards/task-lifecycle.md)、[文档治理](../standards/doc-governance.md)
  与压缩后的 todo；壳的完整内容见提交 `b42540b`（`git show b42540b:docs/plans/2026-09-21-ledger-plan-carrier-contract.md`），
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-005 行。
- PLAN-002 Axiom-Flow 配置简化与解析重构（Axiom-Flow）已完成（2026-09-21），计划按 `Retain` 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-17-axiom-flow-refactor.md)（v2 重构基线：
  2026-09-17 七条用户裁决 + af_* 真机迁移里程碑 + 8 条成功标准逐条判定）；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-002 行。
- PLAN-006 解析状态语义收口（Axiom-Flow）已完成（2026-09-21），计划按 `Delete` 删除；语义事实源
  固定为 [ADR 0007](../adr/0007-parse-status-semantics.md) + [api.md 状态机表](../architecture/api.md)
  + [database-design.md 列语义](../architecture/database-design.md)
  + [parsing-management.md 的 `PM-8`~`PM-14`](../design/parsing-management.md)
  （2026-09-23 叙述框架重整前编号为 S1~S7，原载 `parsing-pipeline.md`），运维边界见
  [operations.md](../guides/operations.md)；壳的完整内容见提交 `9034a19`
  （`git show 9034a19:docs/plans/2026-09-21-parse-status-semantics.md`），关闭证据见
  [completed.md](../trackers/completed.md) PLAN-006 / DEFECT-006 / DEFECT-007 行。
- PLAN-004 解析产物按版本划分（Axiom-Flow）已完成（2026-09-22，根侧四条确认全部接受后关闭），
  计划按 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-20-req080-parse-version-layout.md)（保留
  根仓库需求对表 R1~R10、本仓库现状约束 L1~L10 与条文 V1~V10 的**推导依据**）；运行时事实源不在壳内，
  而以晋升后的 [parsing-pipeline.md 版本条文 `PP-14`~`PP-18`](../design/parsing-pipeline.md)
  与 [parsing-management.md `PM-6`/`PM-11`](../design/parsing-management.md)、
  [api.md](../architecture/api.md)、[database-design.md](../architecture/database-design.md) 为准；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-004 与 REQ-080 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-20-req080-parse-version-layout.md` 处须改指上述
  归档位置（迁移文件头注释 `20260920_0002_parse_version_scoping.py` 已随本批更新）。
- PLAN-003 解析真机打通：设计偏差核对与契约修正（Axiom-Flow）已完成（2026-09-22，用户裁决取 (A)
  「先转承载再关」后关闭），计划按 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-20-parse-live-bringup.md)（保留 W0 冻结清单与
  留档哈希、23 项偏差台账、契约条文晋升 `design/`+`architecture/` **之前的推导依据**，以及 R0/R1/R2 三个
  真机窗口的逐条取证表）；运行时事实源以晋升后的
  [parsing-pipeline.md](../design/parsing-pipeline.md)、[api.md](../architecture/api.md)、
  [database-design.md](../architecture/database-design.md)、[operations.md](../guides/operations.md) 为准。
  **六条活行已改挂两份新计划**：PLAN-007（全本窗口分块提交 + 空白页合法化，承载 REQ-086/087、ARCH-009/019）
  与 PLAN-008（页级失败清单与 jobs 列表端点，承载 REQ-088、ARCH-005/006）；关闭证据见
  [completed.md](../trackers/completed.md) PLAN-003 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-20-parse-live-bringup.md` 处须改指上述归档位置
  （本批已同步 `docs/adr/0007`、`docs/trackers/*`、`tmp/probe_mineru_api.py`）。
- PLAN-010 非公开管理端点一键清除（Axiom-Flow）已完成（2026-09-23，W1~W7 全绿后关闭），计划按 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-23-admin-purge-endpoint.md)（保留真机演练的
  dry-run 清单、`af_*` 整表备份锚点与首轮 K6 缺陷的归因链，以及「不对外开放」如何被拆成可测三件事的
  推导依据）；运行时事实源不在壳内，而以晋升后的 [api.md「非公开管理面」](../architecture/api.md)、
  [database-design.md 条文 9 例外](../architecture/database-design.md)、
  [parsing-management.md 端点行](../design/parsing-management.md) 与 [operations.md runbook](../guides/operations.md) 为准；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-010 / ARCH-024 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-23-admin-purge-endpoint.md` 处须改指上述归档位置
  （本批已同步 `docs/trackers/todo.md`、`docs/trackers/project-status.md`）。
- PLAN-011 清除端点书目录枚举改深度无关（Axiom-Flow）已完成（2026-09-23，当日立项当日关闭），计划按
  **`Retain`** 归档至 [history/plans/2026-09](../history/plans/2026-09/2026-09-23-purge-book-dir-depth-agnostic.md)
  （保留业务侧首次 dry-run 暴露的缺陷读数、S1~S6 逐条判定与 **2026-09-23 业务侧全项目真删 + 书目回灌**
  的 D 类留档：`deleted`={17,24,1015,2,4 目录}、`parsed/` 文件数 7379 ⇒ 0、回灌 `synced:17`）；
  运行时事实源不在壳内，而以晋升后的 [api.md 磁盘作用域段](../architecture/api.md)（识别只看产物标记）与
  [operations.md runbook 第 2 步](../guides/operations.md)（核对 `targets` 层数形状）为准；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-011 / ARCH-026 行。
  **归档判定本为 `Delete`，因壳与修复均未提交 ⇒「Git 锚点有效」不成立而改判 `Retain`；
  2026-09-24 清理轮结案**：壳已随 `fb7eb5a` 入库，但本行留档属**已执行的 D 类数据操作**
  （业务侧全项目真删 + 书目回灌）⇒ `Retain` 为终态判定，不再重判。**
- PLAN-007 全本解析按页窗口分块提交与空白页合法化（Axiom-Flow）已完成（2026-09-22，S1~S5 逐条判定后
  关闭；同日用户裁决「环境侧与根侧复测归根仓库图像模型维护线」⇒ 原条文里的根侧确认移出本计划），
  计划按 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-22-req086-whole-book-window.md)（保留 W0 窗口
  协议真机冻结结论、实施设计裁决 1~5、W6 四次尝试的归因更正链与 b05 全本真机读数、REQ-086/087 回执
  正文、台账历史归档）；运行时事实源以晋升后的
  [parsing-pipeline.md](../design/parsing-pipeline.md)、[model-integration.md](../design/model-integration.md)、
  [api.md](../architecture/api.md)、[operations.md](../guides/operations.md) 为准；关闭证据见
  [completed.md](../trackers/completed.md) PLAN-007 / REQ-086 / REQ-087 / ARCH-009 / ARCH-019 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-22-req086-whole-book-window.md` 处须改指上述归档位置
  （本批已同步 PLAN-008 壳、`docs/trackers/*` 与 PLAN-003 归档壳）。
- PLAN-012 设计叙述框架重整（Axiom-Flow）已完成（2026-09-23，当日立项、W2~W7 当日执行完毕后关闭），
  计划按 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-23-design-narrative-framework.md)（保留
  S1~S6 逐条判定、「现有章节 → 新骨架」**逐节映射**与三条硬规则的立法理由——即六段归属**为什么这样分**
  的唯一推导依据）；运行时事实源不在壳内，而以重整后的
  [parsing-pipeline.md](../design/parsing-pipeline.md)、[parsing-management.md](../design/parsing-management.md)、
  [model-integration.md](../design/model-integration.md) 与 [design/index.md](../design/index.md) 为准；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-012 行，长期行 ARCH-010 保持 `In Progress`。
  **归档判定本为 `Delete`，因壳与 `docs/design/` 改动均未提交 ⇒ 改判 `Retain`；
  2026-09-24 清理轮结案**：壳已随 `23664c2` 入库，而「现有章节 → 新骨架」逐节映射与三条硬规则的
  立法理由只在壳内、`design/` 不留推导过程 ⇒ `Retain` 为终态判定，不再重判。**
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-23-design-narrative-framework.md` 处须改指上述归档位置
  （本批已同步 `docs/design/` 三篇「叙述框架」行与 `docs/trackers/project-status.md`）。
- PLAN-014 V0.1 固定文档形式正规化（Axiom-Flow）已完成（2026-09-23，W0~W8 全绿后关闭；执行
  [ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md)），计划按 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-23-arch010-v01-architecture-form-normalization.md)
  （保留 S1~S7 逐条判定、25 例守护变异复验、零丢失证明的**判据替代说明**（反引号 token 覆盖比对取代
  8-gram）、七篇 `确认状态` 转正的逐篇依据，以及 `code-map.md` 备注列「删 → 存于哪条条文」的逐条登记）；
  运行时事实源不在壳内，而以正规化后的 [architecture 四篇](../architecture/index.md)、
  [design 三篇](../design/index.md) 与守护 `tests/contract/test_fixed_document_form.py` 为准；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-014 行，长期行 ARCH-010 保持 `In Progress`。
  **归档判定 `Retain`（立项时即为 Retain）**：S1~S7 判定与 25 例变异复验属不可再生证据；
  2026-09-24 清理轮结案**：壳已随 `6bf2ff9` 入库 ⇒ 立项时「锚点不成立」的附带理由已消失，
  但 Retain 判定本身不变（守护复验记录不可再生）。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-23-arch010-v01-architecture-form-normalization.md` 处须改指
  上述归档位置（本批已同步 [ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md) 的「取代」节链接）。
- PLAN-008 页级失败清单与 jobs 列表端点（Axiom-Flow，REQ-088 承载计划）已完成（2026-09-24，W1~W7 判据
  全部消耗后关闭），计划按立项判定 **`Delete`** 删除：端点契约事实源已晋升
  [architecture/api.md](../architecture/api.md) ③ 类两节与 [code-map.md](../architecture/code-map.md)，
  逐条判定与真机复验读数见 [completed.md](../trackers/completed.md) 的 PLAN-008 / ARCH-005 / ARCH-006 /
  REQ-088 四行；壳正文可 `git show 8fca39b:docs/plans/2026-09-22-req088-page-jobs-list.md` 找回
  （`8fca39b`＝复验读数与 ARCH-006 改判入册，`21bc8cf`＝本方侧收口，`650cfef`/`a710ddf`＝端点实施）。
  **S5 的两面判定**：ARCH-005 页清单与质量信号经 8903 真实消费 ⇒ Achieved；ARCH-006 块编辑在重建后的
  当代际无写入样本 ⇒ 按用户裁决以清除前那次 PASS 改判关闭，记 Partial，当代际证据移交根仓库下一窗口。
- PLAN-013 V0.1 收口与文档清理（Axiom-Flow，A 类）已完成（2026-09-24，S1~S5 逐条判定后关闭），计划按
  立项判定 **`Delete`** 删除：收口结论以 [completed.md](../trackers/completed.md) 的 PLAN-013 / ARCH-001
  两行与 [project-status.md](../trackers/project-status.md)「当前版本」段为事实源（壳立项时即自证「无
  不可替代证据」，W0 实测读数与 D1~D4 裁决已分别入二十/二十一轮与 completed 行）。壳正文可
  `git show 268b7d2:docs/plans/2026-09-23-v01-closeout-doc-cleanup.md` 找回（进场裁决 D1~D4、工作项 W0~W6
  逐条状态、B1~B6 收口步骤与阻塞四要素皆在锚点提交内）。**v0.1 主线四行由此全部关闭**，ARCH-011 批量
  与 v1.0 线（PLAN-009）为下一里程碑的前置。
- PLAN-016 存储规范对齐实施（Axiom-Flow，B 类，承载 ARCH-034）已完成（2026-09-26，S1~S7 逐条判定、
  W8 回执与六步收口执行后关闭），计划按 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-24-storage-convention-alignment.md)（保留
  C1~C7 逐条对账表与 Q1~Q4 裁决记录、W7 经批清理的前后指纹（已执行 D 类数据操作，不可再生）、
  变异复验 M1~M4 与第二窗口独立复验链、ADR 0009 决定 5 措辞按实现收窄的记录）；运行时事实源不在壳内，
  而以 [ADR 0009](../adr/0009-data-root-must-be-explicit.md)、`overview.md` 数据根顶层白名单哨兵区、
  [parsing-pipeline.md `PP-4`/`PP-6`](../design/parsing-pipeline.md)、
  [operations.md 数据目录表与留档区核对步](../guides/operations.md) 与守护
  `tests/contract/test_storage_conventions_governance.py` 为准；关闭证据见
  [completed.md](../trackers/completed.md) PLAN-016 / ARCH-034 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-24-storage-convention-alignment.md` 处须改指上述
  归档位置（本批已同步 `docs/adr/0009`、`docs/trackers/*`）。
- PLAN-015 ADR 0018 数据根治理承接确认包（Axiom-Flow，A 类，承载 REQ-095）已完成（2026-09-26，
  A1~A4 全部判「一致」、REQ-095 回执正文成文后关闭），计划按立项判定 **`Delete`** 删除：
  四条二元判定与回执正文以提交 `a97ccc7` 为锚点，可
  `git show a97ccc7:docs/plans/2026-09-24-adr0018-data-root-alignment-request.md` 找回（A1~A5 承接确认表、
  S1~S4 判定与「根仓库 REQ-095 回执正文」全文皆在锚点提交内）；运行时事实源不在壳内，判定所据的
  落盘规则以 [ADR 0009](../adr/0009-data-root-must-be-explicit.md) 与根仓库 ADR 0018（只读参照）为准；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-015 / REQ-095 行；根仓库按判据
  「A1~A4 回执后关闭」自读登记其 REQ-095 行。**跨项目承载由此清零**：v1.0 主线下临时承接项仅剩
  PLAN-017（承载根侧 REQ-097 解析产物根视图清理，活跃计划；该条已于同日收口批关闭，见下条）。
- PLAN-017 解析产物根视图清理实施（Axiom-Flow，B 类，承载根侧请求 REQ-097）已完成（2026-09-26，
  S1~S5 本地判据逐条判定、REQ-097 回执正文成文后关闭），计划按立项判定 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-26-parsing-root-view-cleanup.md)（保留
  R1~R5 实施锚点、字段核查表改判与偏差 D1~D3 登记、S1~S5 判定表与「REQ-097 回执正文」全文——
  根仓库据此自读登记关闭其 REQ-097 行）；运行时事实源不在壳内，而以
  [parsing-pipeline.md PP-2/14/15/16](../design/parsing-pipeline.md)、
  [api.md](../architecture/api.md)、[database-design.md](../architecture/database-design.md) 与迁移
  `20260926_0001` 为准；关闭证据见 [completed.md](../trackers/completed.md) PLAN-017 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-26-parsing-root-view-cleanup-request.md` 处须改指上述
  归档位置（本批已同步 `docs/trackers/*`；REQ-097 真机两项移交 ARCH-011 行；该行已于 2026-09-30 关闭，项①于 2026-09-28 执行、项②改挂 PLAN-018 真机面）。
- PLAN-021 工作区目录卫生清理轮（Axiom-Flow，B 类，零 `src` 行为改动）已完成（2026-09-29，S1~S6 逐条判定＋D 类前后指纹留档后关闭），计划按立项判定 **`Retain`** 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-29-workspace-directory-hygiene.md)（保留 Q1~Q4 裁决落地表、W3 清空 `data/` 的前后指纹与 3 份备份 sql 的 sha256 前缀（**已执行的 D 类数据操作，不可再生**）、S1~S6 判定表、变异复验 M1~M5 与「语义损失注记」——Q2 全量改指后历史面路径不再解析到证据，该代价的唯一留档处）；运行时事实源不在壳内，而以
  [ADR 0012](../adr/0012-workspace-directory-hygiene.md)、
  [overview.md「仓库工作区目录卫生」](../architecture/overview.md)、
  [code-map.md](../architecture/code-map.md) 的 `tmp/`/`logs/`/`scripts/` 行、
  [operations.md 数据目录表与日志守则](../guides/operations.md)、
  [local-dev.md](../standards/local-dev.md) 与守护 `tests/contract/test_storage_conventions_governance.py` 为准；关闭证据见 [completed.md](../trackers/completed.md) PLAN-021 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-29-workspace-directory-hygiene.md` 处须改指上述
  归档位置（本批已同步 `docs/adr/0012` 与 `docs/trackers/*`）。

- PLAN-020 `af_*` 表族措辞收口与守护断言（Axiom-Flow，B 类，PLAN-018 W2 加表前置评审轮）已完成（2026-09-29，S1~S6 逐条判定后关闭），计划按关闭时改判 **`Retain`**（原立项判 Delete；改判理由＝加表评审判据唯一落点＋不可再生变异复验）归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-29-af-table-family-governance.md)（保留「新表结构与表族关联」评审节、表数演进对账与两条准入守则、W1 口径位置定判表、W2 先红与变异复验记录、W4 放行结论）；运行时事实源为 [database-design.md](../architecture/database-design.md)（表清单节＋准入守则）与守护
  `tests/unit/test_db_table_family_governance.py`；关闭证据见 [completed.md](../trackers/completed.md) PLAN-020 行。
  **壳的路径已变更**：凡引用 `docs/plans/2026-09-29-af-table-family-governance.md` 处须改指上述归档位置（本批已同步 PLAN-018 壳与 `docs/trackers/*`）。
