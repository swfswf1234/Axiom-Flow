# 测试架构与门禁

状态：Current
确认状态：已确认
最后更新：2026-09-21
治理对象：测试分层、门禁组成、测试编写约定
依据：QED-Tracker `docs/standards/testing.md` 治理模式（[ADR 0003](../adr/0003-standards-governance-alignment.md)）
关联测试：`tests/contract/test_test_suite_governance.py`

## 目的与边界

探索项目采用分层但轻量的测试策略：单元测试覆盖纯逻辑，集成测试用 mock 覆盖外部服务，冒烟
测试在本地真实链路验证。无远端 CI（本仓库唯一门禁是本地门禁）。

## 测试分层

| 层 | 目录 | 内容 | 执行 |
| --- | --- | --- | --- |
| 单元 | `tests/unit/` | schemas 校验、质量信号计算、状态机、哈希/文件名 | 门禁必跑 |
| 集成 | `tests/integration/` | 编排层对外部服务的调用（mock 服务代替，无 GPU 也可跑）；真 MySQL 提交边界用例需 `QED_TEST_MYSQL=1` 显式 opt-in，缺开关时 skip（[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)） | mock 面门禁必跑；MySQL 面本地手动 |
| 冒烟 | `tests/smoke/` | 真实链路：单 PDF 小样含公式跑通 + 兜底触发 | 本地手动，`@smoke` 标记 |
| 契约 | `tests/contract/` | 文档/标准/ADR/代码映射守护 | 门禁必跑 |

## 门禁

本地唯一门禁：

```powershell
pytest tests -q
ruff check src tests
```

- 推送到 `release` 或合并回 `main` 前必须完成本地门禁，不能把分支已推送等同于已验证。
- 不引入远端 CI（GitHub Actions 等）；CI 相关配置不得新增。

## 测试编写约定

- 测试与被测代码保持同一目录结构命名（`test_<模块>.py`）。
- 冒烟测试必须能单独运行且真实调用外部服务（需要 WSL 容器在线时先检查健康）。
- 契约测试守护文档体系：活跃设计集合、架构文档集合、标准集合、ADR 编号、code-map 映射。
  文档体系变更必须同步契约测试。
- 默认测试不得访问公网、不得要求 API key、不得调用外部模型、不得写运行数据库；TLS 校验
  默认开启。**唯一例外**（[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)）：
  `integration` 层在 `QED_TEST_MYSQL=1` 时可连本机回环 MySQL，但只准使用 `qed_test` 库内本仓库
  自有的 `af_*` 四表与 `alembic_version`（夹具逐名断言、连接后硬校验 `SELECT DATABASE()`，且绝不
  `DROP DATABASE`），数据根仍必须是 `tmp_path`；缺该开关时整层 skip，默认门禁零外部依赖不变。

## 执行与门禁

- 测试分层与门禁配置变更后运行 `tests/contract/test_test_suite_governance.py`，再运行全量
  门禁（见[开发指南](../guides/development.md)）。
- 契约测试守护面变化时同步 `docs/architecture/code-map.md` 与相关设计文档。

## 变更与取代

改变测试分层、门禁组成或隔离边界时必须先新增 ADR；措辞、链接和不改变语义的整理可直接修改。
