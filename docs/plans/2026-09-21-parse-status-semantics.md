# PLAN-006：解析状态语义收口（parse-status-semantics）

状态：In Progress
任务类型：B
最后更新：2026-09-21
关联 ADR：[ADR 0007](../adr/0007-parse-status-semantics.md)（本计划实施其决定 1~7）
关联设计：[parsing-pipeline](../design/parsing-pipeline.md)（错误处理与终态表）、[parsing-management](../design/parsing-management.md)（书级进度）、[database-design](../architecture/database-design.md)（af_books/af_parse_jobs 列）、[api](../architecture/api.md)（对外契约）、[code-map](../architecture/code-map.md)
关联 Tracker：`docs/trackers/todo.md` 的 PLAN-006 / DEFECT-006 / DEFECT-007 / ARCH-003 / REQ-086
归档判定：Delete（结论已晋升 ADR 0007 与固定文档；执行留痕与判据入 `completed.md`）

## 目标与成功标准

把用户 2026-09-21 的三项裁决（A1 三态 + `failed_pages`、B1 进度/结局分列、C1 启动期归一）落成
**代码 + 迁移 + 成文契约**，使「部分成功」在任务表与书目表给出一致答案、假活动态有确定收敛时刻。

成功标准（逐条可判）：

1. **S1 判据同源**：`completed` 与版本激活门槛由同一判据/同一函数产出；构造「目标页有失败」的样本后，
   任务状态必为 `partial` 且 `active_job_id` 不变，且「`completed` 但未激活」在测试中被证明不可能出现。
2. **S2 失败页结构化**：`af_parse_jobs.failed_pages` 为整型数组，含 3 页失败的真实形状可整份读出，
   不再依赖 `error` 字符串（字符串仍写人类摘要）。
3. **S3 书级分列**：`parse_status` 只出 `pending`/`parsing`；结局只出 `last_parse_result`。同一次运行后
   两字段组合必属成文枚举，且书 `failed`/`partial` 时旧生效版本照样可读（可见性由 `active_job_id` 决定）。
4. **S4 启动期归一**：以「预置 `running`/`queued` 行 → 冷启动服务」的测试证明归一为 `interrupted`，
   书目进度归 `pending`，且归一发生在受理请求之前；单实例前提写入运维指南。
5. **S5 契约同步**：`api.md`、`database-design.md`、`design/parsing-pipeline.md`、`code-map.md` 与实现
   一致，架构/设计/映射三类文档门禁语义通过。
6. **S6 迁移可降级**：`20260921_0001` 在 `qed_test` 上 upgrade/downgrade 往返通过，回填规则有断言
   （现 `parse_status=completed/failed` → `last_parse_result`，`parsing` → `pending`，历史任务状态不改写）。
7. **S7 台账收口**：DEFECT-006/007 关闭、ARCH-003 解除裁决阻塞、REQ-086 的「数据来源」子项标记为
   已具备（端点仍不在本计划范围）。

## 范围与非目标

**范围**：`schemas.JobStatus`/`ParseStatus`、`pipeline` 终态判定与写回、`jobs`/`api.main.lifespan`
启动期归一、`db/models` 两列 + 仓储写回、迁移 `20260921_0001`、对外响应模型（`ParseJob`/`BookOut`）、
四份固定文档与设计文档、单元/契约/集成测试。

**非目标**：
- 不新增 `GET /api/v1/parse-jobs` 列表端点与页级失败清单端点（REQ-086 范围，本计划只备好数据来源）。
- 不改 `pages_done` 的既有口径（仍为「本任务作用域已解析页数」），不做书级进度重定义。
- 不引入心跳列与后台回收线程（ADR 0007 决定 4 的备选，多实例部署前不实施）。
- 不动分块、归一化、坐标契约与版本目录布局。
- 不做真机重解析（B1/C1 的真机验证随下次已获批窗口顺带执行，不单独开窗口）。

## 前置条件

1. ADR 0007 已 Accepted（本计划其实施载体）。
2. 分支 `release`；工作树干净；门禁基线为 376 passed / 10 skipped。
3. 迁移只在 `qed_test`（测试）与本地 `qed`（真机，需用户逐步批准）执行；遵循 ADR 0005 隔离边界
   （只 DROP 本方自有 `af_*` 四表 + `alembic_version`，绝不 `DROP DATABASE`）。
4. 真机 MySQL 可达性经 `QED_TEST_MYSQL=1` 定向验证；缺开关时相关测试 skip（不得伪报为通过）。

## 工作项

| # | 项 | 落点 | 红灯判据（先写测试） |
| --- | --- | --- | --- |
| W1 | 枚举扩展：`JobStatus` 增 `partial`/`interrupted`；`ParseStatus` 收缩为 `pending`/`parsing`；新增 `LastParseResult` | `schemas.py:69-109` | 单元：`ParseStatus` 不再含 `completed`/`failed`；`JobStatus` 终态集合为成文四值 |
| W2 | 终态判据同源：`completed` ⟺ `_version_is_complete(...)`；`partial` ⟺ 有成功但未齐备；`failed` ⟺ 零成功或引擎异常 | `orchestrator/pipeline.py:422-447` | 单元：20 页目标 17 成功 → `partial` 且 `active_job_id` 未变；「`completed` 且未激活」断言不可构造 |
| W3 | `failed_pages` 结构化落库 + `progress.failed` 计数 | `db/models.py`、`db/repositories.py`、`pipeline._mark_failed` | 单元：3 页失败 → 数组恰为那三页且顺序稳定；`error` 摘要仍非空 |
| W4 | 书级写回分列（进度/结局） | `BookRepository.update_parse` 拆分 + `pipeline` 终态写回 | 集成：同一次运行后 `(parse_status,last_parse_result)` 组合 ∈ 成文枚举；书 `partial` 时旧版本端点仍 200 |
| W5 | 迁移 `20260921_0001`：两列 + 回填 + 可降级 | `src/axiom_flow/migrations/versions/` | 集成：upgrade→downgrade→upgrade 往返；回填断言（`completed`/`failed` 平移、`parsing`→`pending`、历史 job 状态不变） |
| W6 | 启动期归一（单实例前提） | `api/main.py` lifespan + `ParseJobRepository.reconcile_interrupted()` | 集成：预置 `running`+`queued` 行 → 冷启动后 `interrupted`、书目归 `pending`/`interrupted`、发生在校验首个请求之前 |
| W7 | 对外响应与契约文档：`ParseJob`/`BookOut` 字段、`api.md` 端点与错误语义表、`database-design.md` 列注释、`design/parsing-pipeline.md` 终态与错误表、`code-map.md` 状态 | `schemas.py` 输出模型、四份文档 | 契约：`GET /parse-jobs/{id}` 响应含 `failed_pages`；`GET /books` 含 `last_parse_result` |
| W8 | 运维与部署边界成文：单实例前提、归一时刻、`interrupted` 的续跑方式（同 job_id 重放） | `docs/guides/operations.md` | 文档门禁 + 人工核对（无未落地命令） |
| W9 | 台账收口（ADR 0006 六步）：todo 行压缩迁移、`completed.md`、`plans/index.md`、`project-status.md` 刷新 | `docs/trackers/`、`docs/plans/index.md` | `test_tracker_governance.py`、`test_plan_governance.py` 全绿 |

## 验证与验收

- **定向测试**：`tests/unit/test_schemas.py`、`tests/unit/test_orchestrator.py`、
  `tests/unit/test_db_migrations.py`、`tests/contract/test_api_v1_contract.py`、
  `tests/integration/test_parse_persistence.py`（MySQL 双 Session，`QED_TEST_MYSQL=1`）。
- **回归门禁**：`pytest tests -q` + `ruff check src tests scripts` 全绿（基线 376 passed / 10 skipped，
  新增用例后总数只增不减）。
- **文档门禁**：`test_architecture_documents.py`（api/database-design 改动）、`test_design_documents.py`
  （parsing-pipeline 改动）、`test_code_document_mapping.py`（code-map 改动）、
  `test_adr_governance.py`（ADR 0007 登记）、`test_plan_governance.py`、`test_tracker_governance.py`。
- **真机（可选、随窗口）**：下次已获批窗口内顺带 `alembic upgrade head` + 冷启动服务，取证幽灵行
  `5f43593a12cc` 被归一为 `interrupted`。**本计划不以真机结果为关闭判据**。

## 回滚

- 代码与文档：`git revert` 本计划提交即可。
- 迁移：`alembic downgrade 20260920_0002` 丢弃两列（新增列不被旧代码读取，回滚后旧键语义不恢复，
  属声明风险）；无数据行删除，`failed_pages` 与 `last_parse_result` 为可再生派生信息。
- 归一行为：随代码回滚自然消失；已被写成 `interrupted` 的历史行不回收（它是事实，不是损坏）。

## 关闭与归档

- 关闭条件：S1~S6 全部达成且门禁全绿、S7 台账收口完成。**不依赖**真机窗口与根侧回执。
- 收尾：按 ADR 0006 六步（承载文档写结论 → todo 行压缩关闭 → `completed.md` 迁移 → `plans/index.md`
  登记 → `project-status.md` 刷新 → 全量门禁）。归档判定 Delete：语义事实源固定为 ADR 0007 +
  `database-design.md` + `api.md` + `design/parsing-pipeline.md`，Git 锚点有效，故关闭后删除计划壳。
- 关闭后解锁：ARCH-003（裁决依赖解除）、REQ-086（数据来源已成文，端点另行立项）；DEFECT-006/007 入
  `completed.md`。
