# V0.1 固定文档形式正规化（architecture 体裁与可对账契约）

状态：Completed
任务类型：B
最后更新：2026-09-23
关联 ADR：[ADR 0008](../../../adr/0008-fixed-doc-form-and-metadata-genres.md)（体裁化元数据、统一骨架、
正文去日期化、可对账契约表）
关联设计：`docs/design/` 三篇的骨架与条文表是本轮的**形状范本**（六段骨架 + 就地条文编号），
本轮只把 `architecture/` 对齐到同一套体裁规则，不改动任何契约事实
关联 Tracker：`docs/trackers/todo.md` 的 PLAN-014（本计划是它的承载文档）与 ARCH-010（长期行，
本轮是它的实质执行轮）；主线聚合行 ARCH-001 的判据本轮不变
归档判定：Retain（版本末期形式重整的推导依据与逐条判定需留档，且守护改造的变异复验证据不可再生）

## 目标与成功标准

**目标**：把 `docs/architecture/` 提升到与 `docs/design/` 同等的形式严肃度——一套可被守护穷举的
元数据体裁、统一的四段骨架、零过程叙述的正文、能从运行态真源对账的契约表；并在 v0.1 收口点
把固定文档的 `确认状态` 统一处置。

| 编号 | 成功标准（可检验） |
| --- | --- |
| S1 | `architecture/` 四篇正文的元数据**逐字段**符合 ADR 0008 的体裁分类，`Current` 不再出现在契约正文类，映射类不再出现 `实现状态` |
| S2 | 三篇契约正文（overview / api / database-design）与 `code-map.md` 的 H2 等于四段骨架白名单，顺序固定 |
| S3 | `architecture/` 与 `design/` 正文**零**任务编号与日期（`追溯` 段白名单除外），`database-design.md` 表清单的「待真机迁移」矛盾消除 |
| S4 | `api.md` 每端点一节 `#### METHOD /path`（四段骨架占 H2、接口四类占 H3，故端点落 H4），守护从 `app.routes` 双向对账；`database-design.md` 列清单与 SQLAlchemy 模型双向对账 |
| S5 | `code-map.md` 只保留映射五元组 + 反向条文编号，备注列不再复述行为判据，被删事实逐条确认在 `design/` 条文表存在 |
| S6 | `architecture/` 4 篇 + `design/` 3 篇的 `确认状态` 统一转「已确认」，转正依据按治理规范条件逐篇记录 |
| S7 | 新守护每条断言各做一次变异转红复验，全量门禁绿 |

## 范围与非目标

**范围**：`docs/standards/doc-governance.md`（元数据节按体裁细化 + 新增骨架与对账节）、
`docs/architecture/` 五篇、`docs/design/` 三篇的 `确认状态` 行、`docs/adr/0008-*`、新增
`tests/contract/test_fixed_document_form.py` 与 `test_architecture_documents.py` 的对账扩展。

**非目标**：不改任何 API/DB/引擎契约事实；不改 `docs/` 目录结构（不新建目录，阻止项）；不重跑
真机窗口、不触碰运行数据库；不改 `design/` 三篇的条文内容与条文编号（只动 `确认状态` 与必要的
指向 `architecture/` 的措辞）；不把「本轮主攻」从 PLAN-013 抢过来（本轮以 ARCH-010 长期行承载，
PLAN-014 为支线行）。

## 前置条件

1. **与 PLAN-013 的并发边界（2026-09-23 已对齐）**：该轮把 `docs/design/`、`docs/trackers/*` 与
   `tests/contract/test_design_documents.py` 判为本轮在写，并将其 W2/W3 批次（PLAN-008 收口）
   `Blocked` 到本轮入库之后。本轮**不代做该批**，只在 W8 收口时把「恢复条件已满足」写进登记节，
   由该轮照其预置的 B1~B6 执行。
   该轮预置的 B4 结构冲突（本行若是 `支线`，则 ARCH-001 的「全部支线行关闭」判据被本行卡住）按
   **出路 (i) 的等价解法**消解：PLAN-014 登记为 `主线` 计划行（与 PLAN-008/009/013 同构），
   不进入 ARCH-001 的支线集合，因此 v0.1 能力面的收口不被文档形式轮阻塞。
2. `architecture/` 与代码的一致性已由 PLAN-013 一轮核对完成，本轮才允许把「不一致」当作**形式**
   问题处理，而不是偷偷修正事实。
3. ADR 0008 处于 Accepted（已完成）。
4. 本地门禁基线绿，作为「本轮引入的红灯」的对照点。

## 工作项

| 项 | 内容 | 产物 | 状态 |
| --- | --- | --- | --- |
| W0 | 立项：ADR 0008 + 本壳 + `adr/index.md` 登记 + todo 行（不抢主攻） | 四份文档 | **Done（2026-09-23）**：ADR 0008 Accepted、下一编号推进 0009、todo 行登记为 `主线`（见「前置条件 1」）；契约门禁 134 passed |
| W1 | 标准成文：`doc-governance.md` 元数据节按三体裁改写，新增「固定文档骨架与可对账契约」节，「版本末期文档整理」检查清单补「形式与对账」判据 | 标准正文 | **Done（2026-09-23）**：三体裁逐条成文（契约正文类 / 映射与索引类 / 目录索引类），新增骨架白名单与「契约表必须可对账」两节，版本末期检查清单补形式判据 |
| W2 | `overview.md` 重构：四段骨架 + 版本纪元与已交付/未交付矩阵 + 进程端口清单 + 部署图与端到端时序图 + 数据布局（含 `versions/<job_id>/`）+ 读者路径表；标题去版本词 | 概览正文 | **Done**：标题改「系统概览」（不带版本词），四段骨架就位，新增端口/职责清单、Mermaid 部署与端到端时序、数据布局含版本目录、读者路径表指向各唯一事实源 |
| W3 | `database-design.md`：修表清单「待真机迁移」矛盾（改为既成事实 + 迁移序号列）、四段骨架、正文去日期化、每表一张列清单表 | 数据库契约 | **Done**：表清单三条迁移改为既成事实（列迁移序号），四张列清单表与 `### 迁移序列` 就位，「门面 Verified / 正文待办」的矛盾消除，同页锚点补 `#` 前缀 |
| W4 | `api.md`：四段骨架、20 行过程叙述降级到追溯、端点节标题统一 `#### METHOD /path`、两大类分组（公共契约 / 非公开管理面） | API 契约 | **Done（含编辑性修订，见「登记与结论」第 5 条）**：机器可读 `### 端点总表`（18 行公共契约）+ 四个接口类别 H3 + 非公开管理面单独成节，端点小节为 `#### METHOD /path`，`## 失败与兼容语义` 收状态码总表与兼容规则，过程叙述全部降级到 `## 验证与追溯` |
| W5 | `code-map.md`：瘦身为纯映射表（路径/职责/层级/设计关联/测试），行为判据删除并回指 `PP-n`/`PM-n`/`MI-n`，元数据改映射体裁 | 映射表 | **Done**：改为五元组表（路径 / 层级 / 一句话职责 / 事实源条文 / 关联测试），删 1500 字级备注列，元数据转映射体裁（`状态：Current` + `覆盖范围`，不再声明实现状态），被删判据在 `## 验证与追溯` 逐条登记「删→存于 `XX-n`」 |
| W6 | 守护：新增 `test_fixed_document_form.py`（体裁元数据 + H2 骨架白名单 + 正文去日期化），`test_architecture_documents.py` 加运行态对账（`app.routes` ↔ 端点节标题、`Model.__table__.columns` ↔ 列清单），并移除钉措辞的历史断言 | 两条守护 | **Done**：新守护 30 例（体裁元数据 / H2 白名单 / 正文零台账号零日期 / 路由↔总表↔小节三方对账 / ORM↔列清单含可空性、主键、VARCHAR 长度、索引与唯一键名）；`test_code_document_mapping.py` 按新表形状重写；`test_architecture_documents.py` 的硬编码端点清单删除、改为类别锚点 |
| W7 | `确认状态` 统一转正（architecture 4 + design 3），`architecture/index.md` 与 `docs/index.md` 导航同步 | 元数据与索引 | **Done**：七篇正文 `暂定 → 已确认`（依据见「登记与结论」第 4 条），`architecture/index.md` 重写为纯导航（去台账号与版本词、按体裁列表），`docs/index.md` 经核对无需改动 |
| W8 | 收口：本壳逐工作项写结论 → todo 行压缩关闭 → `completed.md` → `plans/index.md` 登记 Retain → `project-status.md` 刷新 → 全量门禁 | 台账六步 | **Done**：见「登记与结论」；全量门禁 `517 passed / 10 skipped` + `ruff check src tests scripts` All checks passed |

## 验证与验收

- 逐条判定 S1~S7，判定结果写在本壳「工作项」表的状态列与文末登记节。
- 守护变异复验：对每条**新**断言各造一处反例（改字段名/挪 H2/塞一个台账号/删一个端点节/塞一列
  不存在的列），确认转红且只转红该断言，然后还原。
- 零丢失证明：`code-map.md` 删除的每条行为判据，先用 `design/` 条文表反向确认同一事实存在，逐条
  登记「删→存于 `XX-n`」；`api.md` / `database-design.md` 的改写区间跑 8-gram 覆盖比对，未命中簇
  逐条人工判定。
- 全量门禁（见 [开发指南](../../../guides/development.md)）：`pytest tests -q` + `ruff check src tests scripts`。

## 回滚

- 全部改动是文档与测试，未提交前回滚＝`git checkout -- <文件>`（先 `git status` 确认不含另一条线
  的在飞文件；只逐文件回滚，不用 `-a`、不碰未跟踪文件）。
- 已提交后的回滚点＝本轮首个提交的父提交；守护与正文同批回滚，避免「守护在校验不存在的形状」。
- 不做任何数据操作、不迁移数据库、不触碰运行数据根，因此无数据回滚面。
- 若 S3 的「修矛盾」被证伪（迁移其实未落真机），则**不改文档措辞**，改判 `实现状态` 回 `In Progress`
  并登记缺陷——门面可以朴素，但不得虚报完成。

## 关闭与归档

按 [任务生命周期](../../../standards/task-lifecycle.md) 六步收口。归档判定 **Retain**：本壳保留 ADR 0008
的落地推导、S1~S7 逐条判定与变异复验证据（这些是后续版本末期轮的参照，不在固定文档里）。
关闭后迁 `docs/history/plans/2026-09/`，`plans/index.md` 登记去处，todo 的 PLAN-014 行移除、
ARCH-010 证据列刷新，`确认状态` 处置结果记入 `completed.md`。

## 登记与结论（收口时填写）

1. **S1~S7 逐条判定：全部达成**。
   - S1：四篇元数据按体裁分化——三篇契约正文类保持「设计状态/确认状态/实现状态/最后更新/关联\*」
     七字段（守护按顺序逐字段断言，且禁止映射类的 `状态：`），`code-map.md` 转映射体裁四字段
     （`状态：Current` / `确认状态` / `最后更新` / `覆盖范围`）并禁止声明设计与实现状态。
   - S2：`overview.md`、`api.md`、`database-design.md`、`code-map.md` 的 H2 序列等于
     `目的与边界 / 当前契约 / 失败与兼容语义 / 验证与追溯`（白名单穷举，缺段或多段即红）。
   - S3：`architecture/` 四篇正文（首段 H2 至追溯段之前）任务编号与具体日期**零命中**；`design/`
     三篇同规则，本轮清理 10 处（MI-1/MI-5/MI-14、PM 两处、PP 三处及篇末日期）。
     `database-design.md` 表清单的「待真机迁移」与头部 `Verified` 的自相矛盾已消除。
   - S4：`api.md` 端点小节为 `#### METHOD /path`；守护三方对账（`app.routes` ↔ 端点总表 ↔ 小节），
     管理面 `POST /api/v1/admin/purge` 单独成节且**不进**公共总表；`database-design.md` 四张列清单
     与 SQLAlchemy 模型双向对账（列名集合、可空性、主键标记、VARCHAR 长度、唯一键与索引名）。
   - S5：`code-map.md` 只剩五元组映射，备注列（单格最长约 1500 字的行为判据）全部删除并在
     `## 验证与追溯` 逐条登记「删 → 存于 `PP-n`/`PM-n`/`MI-n`/`api.md`/`database-design.md` 小节」；
     另登记已退役模块与「只含 `__pycache__` 的 v1 遗留目录不是受管模块」，避免下轮重新误登。
   - S6：`确认状态` 转正见第 4 条。
   - S7：守护变异复验见第 2 条；全量门禁见第 6 条。
2. **变异复验 25 例，每例只转红目标断言**：体裁元数据（改字段名 / 契约篇混入 `状态：` / 映射篇补
   `实现状态`）、H2 骨架（挪段序 / 缺段 / 加段）、正文去日期化（塞一个 `PLAN-0xx` / 塞一个日期）、
   端点对账（总表少一行 / 多一行 / 删一个小节 / 管理面混入公共表 / 重复行）、列清单对账（删一列 /
   改可空性 / 改主键标记 / 改 VARCHAR 长度 / 删索引名 / 删唯一键名）、映射守护（DesignRef 指向
   `history/` / 路径不存在）、目录索引体裁（混入 `确认状态` / 状态取值越界 / 塞台账号 / 塞日期）。
   其中 4 例（W6 收尾时新增的索引体裁两例 + 其重复展开）为收口当日补做，其余 21 例在正文改写轮完成。
   全部还原后 `git status` 无残余。
3. **零丢失证明（判据替代说明）**：本壳原写「8-gram 覆盖比对」，实际改用**反引号 token 覆盖比对**
   ——中文 8-gram 在整段重写后噪声过大，无法逐条人工判定；token 覆盖直接指向事实载体（路径、
   符号、配置键、命令、表列名）。对 `api.md` / `database-design.md` / `code-map.md` / `overview.md`
   以 `git show HEAD:` 取旧文，逐个旧 token 先在**新文**、再在 `docs/{design,architecture,guides,standards}`
   + `completed.md` 全集中做子串检索。结论：未命中项全部落入四类，**无一条契约事实丢失**——
   ① 端点简写形（`GET /books/{id}` 等）被完整路径小节取代并被对账守护覆盖；② 过程叙述
   （`--mode local|qed-engine` 的退役说明、`REQ-001`、`alembic upgrade <rev>` 具体序号）按规则降级，
   命令形状在 `guides/operations.md` 与 `guides/development.md` 仍完整；③ 符号级实现指针
   （`PageRepository.list_states()`、`LEGACY_JOB_ID`、`require_admin_configured()`、测试夹具
   `mysql_store`/`mysql_settings` 等）仍在 `src/`、`tests/` 中，行为判据由 `design/` 条文表覆盖；
   ④ 旧文档自指针（`docs/adr/000x-*.md` 写成代码而非链接）改为 markdown 链接。
   另核对两处易误判的边界事实仍在文：8900 启动快照呈现数据库能力（`database-design.md` 当前契约段）、
   单一占用闸门的 `queued_tasks + processing_tasks == 0` 与 900 秒等待（`api.md` 两处 + MI-12）。
4. **`确认状态` 转正逐篇依据**（治理规范「转正评审触发条件」）：
   - `overview.md` / `api.md` / `database-design.md` / `code-map.md`：条件 ②（本计划 Completed +
     全量门禁绿）与条件 ③（`test_fixed_document_form.py` + `test_architecture_documents.py` +
     `test_code_document_mapping.py` 全绿且无 pending 评审标注）同时成立；此外用户在启动本轮时
     显式批准「本轮统一转已确认」（条件 ①）。
   - `parsing-pipeline.md` / `parsing-management.md` / `model-integration.md`：条件 ①（同一显式批准）
     + 条件 ③（`test_design_documents.py` 与本篇体裁守护全绿，条文表逐条带「实现落点 → 守护测试」）。
   - 本轮**未**改动任何一篇的契约事实，故转正只确认「形式与内容经评审可作为事实源」，不含新增事实。
5. **编辑性修订（记录在此，不改契约）**：ADR 0008 决定 4 与 `doc-governance.md`「契约表必须可对账」
   原写 `### METHOD /path`，与本轮已定的四段骨架冲突（H2 归骨架、H3 归四个接口类别，端点再降一级）。
   实现取 `#### METHOD /path`，并同步修 ADR 与该标准节（纯层级修正，语义不变），本壳 S4/W4 措辞同步。
6. **门禁**：`pytest tests -q` → **517 passed / 10 skipped**（新增 `test_fixed_document_form.py`
   30 例、`test_code_document_mapping.py` 5 例改写）；`ruff check src tests scripts` → All checks passed。
   10 例 skip 为默认离线策略下的 MySQL 与真机冒烟层，非本轮引入。
7. **并发边界结论**：PLAN-013 预置的「`git status --porcelain docs/trackers docs/design` 干净后方可
   执行 B1~B6」的恢复条件，本轮已把 `docs/design/` 三篇与 `docs/trackers/todo.md` 改到终态，本轮文件
   落定后该条件对其所有者成立；PLAN-013 自身的 W2~W6 由该轮自行执行，本轮不代做。
8. **遗留（移交，不在本轮修）**：
   - `pyproject.toml` 的 `project.version` 与 `axiom_flow.__version__` 不同源（0.4.0 vs 0.5.0）：
     `api.md` 原先的「同源」措辞已删，改为如实分述两者；是否统一版本号属代码事实，另轮处理。
   - `src/`、`tests/`、`scripts/` 文件头仍有旧条文编号（`V1..V10` / `S1..S6`）约 60 处，属 ARCH-010
     长期行既登遗留与 PLAN-013 W4-② 范围，本轮不代改。
   - `scripts/axiom_flow_service.py` 文件头仍写日期化过程（「配置简化（2026-09-17，PLAN-002）」），
     同上归 ARCH-010。
   - `tmp/` 临时脚本目录未清理（另一条线在飞，本轮不碰）。
9. **关闭与归档执行**：归档判定 **Retain** 成立（推导依据、逐条判定与变异证据不可再生），本壳迁
   `docs/history/plans/2026-09/`，`plans/index.md` 与 `history/plans/2026-09/index.md` 登记去处；
   todo 的 PLAN-014 行移除、ARCH-010 长期行证据刷新，结果迁 `completed.md`。
