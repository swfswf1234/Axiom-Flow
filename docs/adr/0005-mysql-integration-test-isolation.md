# ADR 0005：MySQL 集成测试的隔离边界（qed_test 显式 opt-in）

状态：Draft
最后更新：2026-09-20
关联代码：`tests/support/mysql.py`（待实现，本 ADR 接受后编写）、`tests/conftest.py`（既有
`mysql_settings` / `mysql_store` 夹具入口）
关联测试：`tests/integration/test_parse_persistence.py`（待实现）、
`tests/contract/test_test_suite_governance.py`

## 背景

`docs/standards/testing.md` 与 `.qoder/skills/qed-implement` 的隔离铁律规定：默认测试不得访问公网、
不得要求 API key、不得调用外部模型服务、**不得写运行数据库**，测试只用临时目录。

该铁律在解析持久化上造成了无法验证的契约缺口（DEFECT-001）：

1. `orchestrator/pipeline.py` 的页状态写入全程只 `flush`，唯一 `commit` 在
   `orchestrator/jobs.py:60` 的任务结束时；`ParseJobRepository.update_progress` 生产零调用。
   「进度自持与断点续跑」（`architecture/database-design.md:124/137-139`、
   `design/parsing-pipeline.md:177/190`）因此不成立。
2. 现有 DB 测试（`tests/unit/test_db.py`、`test_db_migrations.py`）**在同一 Session 上**读写，
   共享 Session 使未提交数据依然可见 → 这类缺陷在测试里结构性不可见。`tests/integration/` 当前
   没有任何测试文件。
3. 修复需要「写会话提交前，独立读会话不可见；逐页提交后可见」的**双 Session 断言**，并且必须在
   真正按 MySQL 语义（独立连接、事务隔离）工作的数据库上跑，SQLite 内存库不足以复现。
4. `tests/conftest.py:56-70` 已经引用 `tests.support.mysql` 的 `prepare_mysql_settings` 与
   `isolated_mysql_store` 两个夹具函数，而该模块不存在——即既有设计已预期这条路径，缺的是隔离
   边界的合法依据。

按 `docs/standards/testing.md` 「变更与取代」：改变测试分层、门禁组成或**隔离边界**时必须先新增
ADR。因此本 ADR 先于 `tests/support/mysql.py`、`tests/integration/` 用例与 `testing.md` 修订。

## 决定

1. **允许范围**：`integration` 标记的测试层，在**显式 opt-in** 环境变量 `QED_TEST_MYSQL=1` 时，
   可以连接运行中的本机 MySQL 实例（`QED_DB_HOST`/`QED_DB_PORT`，回环地址），但**只能使用独立
   schema `qed_test`**。`unit`、`contract`、默认 `smoke` 不开此口。
2. **硬校验，不靠约定**：夹具建立连接后必须读取 `SELECT DATABASE()` 并断言等于 `qed_test`，
   不匹配立即失败退出；任何代码路径**不得读取、写入或 DDL `qed` 业务库**。连接参数由夹具自行
   构造，不复用运行库的 `QED_DB_NAME` 值。
3. **自建自删**：`qed_test` schema 由夹具创建、Alembic 升级到 head、用例结束后整体 drop；不假设
   人工预置，也不留残留库。数据只用合成样本，不从 `qed` 拷贝。
4. **缺 opt-in 时 skip 而非 fail**：未设 `QED_TEST_MYSQL=1` 时用例 `pytest.skip`，门禁照常全绿；
   因此默认门禁仍然零外部依赖，隔离铁律的第一句（「默认测试不得写运行数据库」）不被削弱。
5. **数据根同样隔离**：集成测试的 `QED_DATA_ROOT` 指向 `tmp_path` 下临时目录，不读写真实数据根。
6. **冒烟层沿用同一精神**：`tests/smoke/` 需要真实模型服务或真实 MySQL 时，必须由显式 opt-in
   开关触发、默认 skip（W8 沿用），且只写测试专用书籍与 `qed_test`。

## 后果

- **正面**：DEFECT-001 类「不落库」缺陷从结构性不可见变为有回归守护；`af_pages` 页级持久化与
  `progress` 单调性获得真实数据库语义下的断言。
- **代价**：集成测试不再是零环境依赖，需要本机 MySQL 在线才能真跑；CI 无远端门禁，故该层由开发者
  本地显式执行，团队必须记住「默认绿 ≠ MySQL 路径已验证」。
- **缓解**：`pytest` 输出中 skip 计数可见；`docs/guides/development.md` 与 `testing.md` 记录
  `QED_TEST_MYSQL=1` 的执行时机（改动 `orchestrator/`、`db/` 持久化语义后必须跑一次）；夹具在
  连不上 MySQL 时报错信息给出「是环境问题还是遗漏开关」的判别提示。
- **风险边界**：最高破坏性风险是误连运行库 → 由决定 2 的 `SELECT DATABASE()` 硬校验与决定 3 的
  自建自删共同约束；夹具不使用任何带 `DROP`/`TRUNCATE` 的宽权限连接凭据以外的兜底手段，凭据仍走
  现有 `.env` 读取路径（`QED_DB_*`），但库名固定为 `qed_test`。

## 取代

取代：无。
被取代：无。
修订（不改语义主体）：`docs/standards/testing.md` 的「测试分层」integration 行与「测试编写约定」
隔离条目按本 ADR 补充例外句；`.qoder/skills/qed-implement` 隔离铁律条目在 testing.md 更新后视为
同步引用（技能文件不重复登记规则正文）。
