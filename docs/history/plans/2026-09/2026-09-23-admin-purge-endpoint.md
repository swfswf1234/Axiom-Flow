# 非公开管理端点一键清除（af_* 与 QED_DATA_ROOT/parsed/）

状态：Completed
任务类型：B
最后更新：2026-09-23
关联 ADR：[ADR 0005](../../../adr/0005-mysql-integration-test-isolation.md)（库侧只碰自有 `af_*`、绝不 DROP/DDL 的同源纪律）、[ADR 0007](../../../adr/0007-parse-status-semantics.md)（`queued`/`running` 是「在飞」的唯一判据）、[ADR 0004](../../../adr/0004-model-boundary-and-pipeline-ownership.md)（不做静默兜底：管理面开关与凭据缺失一律显式失败）
关联设计：[api.md](../../../architecture/api.md)、[database-design.md](../../../architecture/database-design.md)、[parsing-management.md](../../../design/parsing-management.md)、[code-map.md](../../../architecture/code-map.md)
关联 Tracker：docs/trackers/todo.md（ARCH-024、PLAN-010）
归档判定：Retain（真机演练属「已执行数据操作」记录：删除清单、备份锚点与 dry-run 留档不可从代码重建）

> 2026-09-23 由用户口头需求立项：「需要一个**不对外开放**的接口，一键清除本项目部分数据库数据和
> `QED_DATA_ROOT` 对应解析数据（也可以按照书目清除）」。三项裁决见「进场三项裁决」。

## 目标与成功标准

**目标**：给本仓库一个**默认不存在**的管理端点，一次调用把 af_* 四表的解析侧数据与
`QED_DATA_ROOT/parsed/` 下的产物目录一起清掉，作用域可以是**指定书目**或**全项目**，用于「先清后建」
（批量重解析、探针窗口收尾、本地重置）。它是既有 `DELETE /api/v1/books/{book_id}?purge=true` 的上位能力：
后者一次一本、且按条文 9 保留 `af_parse_jobs` 审计行。

**为什么「不对外开放」不能靠网络**：8902 本来就只绑 `127.0.0.1`（`scripts/axiom_flow_service.py:71-72`），
所以本计划的「不对外」＝**不进根仓库 8900/8903 消费的公共契约面** + **默认不挂载** + **双确认**，三者叠加。

**成功标准**：

| # | 标准 | 判据 |
| --- | --- | --- |
| S1 | 默认状态下该路由**不存在**（未显式启用即 404，不返回 405/403，避免泄漏管理面存在性） | 契约测试 |
| S2 | 启用后必须凭 token 才执行；token 未配置却启用 ⇒ **启动即失败**（不静默放行、不降级为「无凭据可用」） | 契约测试 + 单元（fail-closed 两态） |
| S3 | `dry_run` 缺省为 `true`：只回「将删清单 + 计数」，零删除；真删需 `dry_run:false` 且 `confirm` 逐字匹配 ⇒ 双确认 | 契约测试（缺 confirm / confirm 错值 / dry-run 后计数不变三条） |
| S4 | 作用域守卫：磁盘目标 resolve 后必须在 `QED_DATA_ROOT/parsed/` 之内，库侧只碰 `af_*` 四表且**只用 DML**（无 DDL、无 DROP DATABASE） | 单元（越界路径 400）+ 现有凭据/边界守护 |
| S5 | 有 `queued`/`running` 作业行时拒绝执行（409），不产生「边解析边删」的半删状态 | 契约测试 |
| S6 | 删除结果可见：响应结构化回 `deleted`（表→行数、目录数）与 `failures`，任一步失败**不回滚已成功步、不静默吞**，重跑幂等 | 契约测试（幂等重跑 + 注入失败可见） |

## 范围与非目标

**范围**：

- `schemas.py`：`AdminPurgeIn`（`scope`、`dry_run` 默认 True、`confirm`）与 `AdminPurgeOut`
  （`scope`、`dry_run`、`deleted`、`failures`）。
- `db/repositories.py`：`ParseJobRepository` 增 `delete_for_book`/`delete_all`（现**无任何删除方法**，
  只有 `list_for_book:251`/`list_all:263`）；`BookRepository`/`PageRepository`/`EditRepository` 现只有
  `delete:217`/`delete_for_book:440,533` 的按本删除 ⇒ 各补一个全表清除方法。
- `api/main.py`：**独立管理路由挂载**（建议 `POST /api/v1/admin/purge`），只在 `QED_ADMIN_ENABLED=1` 时注册；
  token 比对用 `hmac.compare_digest`，凭据绝不进日志与异常文本（`AGENTS.md` 隔离铁律）。
- 磁盘面：`QED_DATA_ROOT/parsed/<domain>/<course>/<book_id>/` 整目录（含 `versions/<job_id>/` 与根视图页图；
  页图是硬链接，两个入口一起 rmtree 即回收，无引用计数残留）。
- 文档：`api.md` 新增「非公开管理面」节（含 8900/前端**不得透传**的强制规则一条）、`database-design.md`
  条文 9 加 purge 例外、`design/parsing-management.md` 端点表补一行、`code-map.md` 与
  `guides/operations.md`（备份先行 + D 类窗口口径）同步。

**非目标**：

- 不改既有 `DELETE /api/v1/books/{book_id}`（含其 `purge=true`）的语义与响应形状。
- 不做**单个解析版本**的细粒度清理（旧 `versions/` 保留策略已由 REQ-080 判为「另立 D 类」，本计划不吞）。
- 不做定时任务、批量导入式清理、UI 按钮（前端不消费本端点）。
- 不新增鉴权体系（无用户/角色概念，一把管理 token 足够）；不引入 HTTPS 层（8902 只绑回环）。
- 不做「清除后自动重灌」：重建走既有 sync → ingest → parse 三步，本端点只删不建。

## 前置条件

1. **api.md/database-design 条文草案经用户评审**（本计划正文即草案，见「契约草案」节）——
   `docs/architecture/` 改动属大修改，评审前不动固定文档与代码。
2. 管理面开关与 token 的**环境变量命名冻结**：`QED_ADMIN_ENABLED`（缺省即关）＋ `QED_ADMIN_TOKEN`；
   两者进 `config.py` 的显式映射表，且 token **不进任何响应、日志与异常文本**。
3. 真机取证复用探针窗口的隔离纪律（判例 PLAN-007 W6）：全项目形态只在
   `qed_test` ＋ 隔离探针数据根上演练；业务库 `qed` ＋ 真实 `dataset/parsed/` 的全项目清除属**另一次 D 类批准**。

## 契约草案（评审对象；未评审不进固定文档）

```
POST /api/v1/admin/purge          # 非公开管理面：默认不挂载（404）
Header: X-QED-Admin-Token: <与 QED_ADMIN_TOKEN 比对；不符 401>
Body:   { "scope": {"all": true} | {"book_ids": ["mathanalysis-b05", ...]},
          "dry_run": true,            # 缺省 true：只回清单，零删除
          "confirm": "PURGE" }        # dry_run=false 时必须逐字匹配，否则 400
200: { "scope": ..., "dry_run": ..., "deleted": {"af_books": n, "af_parse_jobs": n,
       "af_pages": n, "af_block_edits": n, "directories": n},
       "failures": ["..."] }          # 人类可读中文，不含绝对路径与凭据
400: 作用域越界 / confirm 缺失或错值 / book_id 含非法字符
401: token 不匹配        409: 存在 queued/running 作业行
```

- **条文 9 例外（用户裁决）**：书目删除保留 `af_parse_jobs` 审计行的既有契约不变；**显式管理清除
  （purge）一并删除 jobs 行**，因为它的目的就是回到干净状态。`af_books` 行同样清除（书目可经
  `POST /books/sync` 从根库重建，不是本仓库的持久资产）。
- **守卫次序**（W6 真机校正后定稿）：`confirm` → 作用域形状与书目落点（⇒ 400）→ 在飞判定（⇒ 409）→ 删除。
- **删除顺序**：先磁盘、后库。磁盘失败 ⇒ 库行原样保留（可重跑，读取面最多 404）；库失败 ⇒ 已删目录不回滚，
  但残行必被下一次清除幂等收掉，且 `failures` 明写。**不做「假装成功」**（ADR 0004）。

## 工作项

| # | 工作项 | 产物 | 门禁 | 状态 |
| --- | --- | --- | --- | --- |
| W1 | 契约条文评审通过后写入 `api.md` 新节 + `database-design.md` 条文 9 例外 + `parsing-management.md` 端点行 | 文档改动（先于实现） | 架构/设计契约测试 | **Done（2026-09-23）**：三处条文落地；「作用域违规」按实现定稿为 **400 整请求零删除**（不是「记 failures 跳过」），并把「书目行落点也要判、目录不存在也判」写进不变式；另补「磁盘只删书目录、不裁剪空父级」与 `DELETE /books` 同口径 |
| W2 | 红灯测试：S1~S6 各条（含 fail-closed 两态、越界 400、409、dry-run 零删除、幂等重跑） | `tests/contract/test_api_v1_contract.py` 新类 + 单元 | 单元/契约 | **Done（2026-09-23）**：`TestAdminPurgeEndpoint` 12 例（W6 后补第 13 例 `test_unsafe_segment_beats_in_flight_conflict`，见 W6 行），先取真红灯（11 failed / 1 passed＝默认不挂载那例本应通过）再实现 |
| W3 | 仓储清除方法（四表按本与全表两形态）＋ `orchestrator/` 或 `application/` 侧 purge 服务函数（磁盘清单枚举 + 守卫） | `repositories.py`、purge 模块 | 单元（SQLite + `tmp_path`） | **Done（2026-09-23，位置改了，理由如下）**：仓储侧四表 `delete_for_book()`/`delete_all()` + `count_active()`；**purge 逻辑落在 `api/admin.py` 而非 `application/`**——`test_architecture_dependencies.py` 明令 `application/` 不得 import sqlalchemy，`orchestrator/` 的职责是解析推进，放清除等于污染两个层的语义 |
| W4 | 管理路由与挂载开关（`QED_ADMIN_ENABLED`）＋ `AdminPurgeIn/Out` schemas ＋ token fail-closed 启动校验 | `api/main.py`、`schemas.py`、`config.py` | 契约 | **Done（2026-09-23）**：`mount_admin()` 在 `create_app()` 内装配（未启用即 `return` ⇒ 404；启用无 token ⇒ `AdminConfigError` 拒绝启动） |
| W5 | 文档与映射收口：`code-map.md` 行、`operations.md` runbook（备份先行、D 类口径、与 `--wsl-holder` 无关） | 文档改动 | 契约 | **Done（2026-09-23）**：code-map 新增 `api/admin.py` 行 + `db/`、`config.py` 行补清除原语与 `QED_ADMIN_*`；`operations.md` 新增「管理面一键清除 runbook」（与 `DELETE /books` 的分工、五步执行纪律、真实根全项目清除＝另一次 D 类批准） |
| W6 | 真机演练：`qed_test` ＋ 探针数据根上跑「按书目」与「全项目」两形态（先 dry-run 留清单）；~~按本形态另在业务库对合成探针书各演练一次~~ | 留档 JSON 清单 + 备份锚点 | `QED_TEST_MYSQL=1` 层 | **Done（2026-09-23）**：`tmp/live_admin_purge_plan010.py` K1~K9 **全 PASS**（`all_pass=True`、`业务零写入=True`、退出码 0），留档 `tmp/probes/purge-20260923/`（`S0_preflight`/`S1_seed`/`backup_af_tables`/`S2_scope_before`/`K1`~`K9`/`S2_verdicts`/`summary.md`）；清除前 `qed_test` 四表整表备份 {af_books:3, af_parse_jobs:4, af_pages:4, af_block_edits:2}；业务库 `qed`（af_books 17 / jobs 24 / pages 1015 / edits 2）与真实 `dataset/parsed`（7379 文件、3.90 GB）前后指纹逐字段相同。**首轮 K6 FAIL 暴露守卫次序缺陷**：真删路径上 409 判定抢在落点守卫之前，脏 `domain_id` 被误报成「解析在飞」；SQLite 契约测试从未把脏行与在飞行放在同一作用域，故只在真机暴露 ⇒ 补回归用例 `test_unsafe_segment_beats_in_flight_conflict` 并固定次序 `confirm` → 作用域（形状＋落点）→ 在飞 → 删除。**作用域已收窄（按 2026-09-22 真机裁决「探针写入只落 `qed_test`」）——业务侧清除是「清理一轮」那一步的独立 D 类批准，不在本工作项内** |
| W7 | 收口（六步，ADR 0006） | 本计划关闭与 Retain 归档 | 全量门禁 | **Done（2026-09-23）**：六步收口，门禁 478 passed / 10 skipped + ruff clean + `QED_TEST_MYSQL=1` 层 8 passed |

## 验证与验收

- 默认门禁零外部依赖：S1~S6 全部可在 SQLite ＋ `tmp_path` 数据根上取证（管理端点不碰模型服务）。
- 显式层：W6 前跑 `QED_TEST_MYSQL=1 pytest tests/integration`；改 `orchestrator/`/`db/` 提交语义即须跑该层
  （`guides/development.md`）。冒烟层与 `QED_SMOKE_LIVE=1` 与本计划无关（无模型调用）。
- 治理面：`test_architecture_documents.py`（api.md 新节锚定）、`test_code_document_mapping.py`、
  `test_tracker_governance.py`（ARCH-024 承载与镜像）。
- **关闭判据**：S1~S6 全绿（本方侧）＋ W6 两形态演练留档；「业务库全项目清除已执行」**不是**关闭判据。

## 回滚

- 代码面：管理路由不注册即等于该端点不存在（默认态），删两条路由 + purge 模块即完全回滚；无迁移、无 DDL。
- 数据面：**清除本身不可回滚** ⇒ 执行纪律＝① `dry_run` 清单先留档；② 真机先 `af_*` 备份
  （`tmp/backups/`，判例 R1/R2 窗口）；③ 真实数据根的全项目清除必须单独获用户 D 类批准，探针根演练不代替。

## 关闭与归档

- **关闭条件**：ARCH-024 行判据达成（端点带契约测试，且真机 `qed_test`＋独立探针数据根按书目与全项目各演练一次（业务库 `qed` 与真实 `dataset/parsed` 上的清除＝「清理一轮」那一步的独立 D 类批准，不属本判据））
  ⇒ 关闭并 Retain 归档 `docs/history/plans/` 对应月份（真机删除清单与备份锚点为不可替代证据）。
- 收尾顺序（ADR 0006 六步）：本文写结论 → todo 行压缩关闭 → `completed.md` 迁移 → `plans/index.md` 登记 →
  `project-status.md` 刷新 → 全量门禁。

## 承载 ARCH-024（非公开管理端点一键清除）

**目的**：让「先清后建」有一条可审计、双确认、默认不存在的运维通路，替代手工 `rm` ＋ 手工 `DELETE`。
**关闭条件**：端点带契约测试，且真机 `qed_test`＋独立探针数据根按书目与全项目各演练一次（业务库 `qed` 与真实 `dataset/parsed` 上的清除＝「清理一轮」那一步的独立 D 类批准，不属本判据）。
**工作项**：W1~W7。
### 台账历史归档（关闭时点）

- 2026-09-23：立项当日完成 W1~W7。真机窗口 `qed_test` + `tmp/probes/purge-20260923/root`（驱动脚本 `tmp/live_admin_purge_plan010.py`，K1~K9 全 PASS、退出码 0，`summary.md` 自证 `all_pass=True` / `业务零写入=True`）；清除前 `af_*` 四表整表备份 `backup_af_tables.json`；业务侧（库 `qed` 与真实 `dataset/parsed`）前后指纹逐字段相同。
- 首轮 K6 FAIL（脏落点被 409 抢先）与修正后的守卫次序、回归用例名见 W6 行——该缺陷只在真机暴露，SQLite 组合不出「脏行 + 在飞行」并存。

**状态**：**Closed（2026-09-23）**。判据达成：`POST /api/v1/admin/purge` 带 13 例契约测试（默认门禁内），真机 `qed_test`＋独立探针数据根上按书目与全项目两形态 K1~K9 全 PASS 并留档，业务库与真实数据根零写入自证通过。
**结论**：①「不对外开放」被拆成可测的三件事——默认不挂载（404 而非 403/405、`/openapi.json` 不收录）、`X-QED-Admin-Token`、`dry_run`＋逐字 `confirm` 双确认；网络面本来就只有回环，不构成本计划的守护点。②库里脏落点必须**先于**在飞判定，否则把配置脏报成「等解析跑完」——该次序由真机 K6 首次暴露，SQLite 用例组合不到一起，故回归网里显式加了脏行＋在飞并存的用例。③清除只删书目录、不裁剪空 `domain/course` 父级，与 `DELETE /books` 和 `operations.md` 同一口径。

### 进场三项裁决（2026-09-23，用户）

1. **形态**＝隐藏管理端点 + 默认关闭（否决「只做运维脚本」与「端点＋脚本包装」）：要真正可一键调用，
   但默认不存在、凭 token、不进公共契约。
2. **`af_parse_jobs` 处置**＝一并清除，条文 9 加 purge 例外（否决「保留审计行」与「无默认参数」）。
3. **顺序**＝先收 PLAN-008（本方侧局部收口：REQ-088 关闭 + 计划转 `Blocked`），再立本行。
