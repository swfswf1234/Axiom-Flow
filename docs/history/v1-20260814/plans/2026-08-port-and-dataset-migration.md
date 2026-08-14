# 计划 2026-08：端口与数据目录迁移执行（port-and-dataset-migration）

状态：Accepted
任务类型：B
最后更新：2026-08-04
关联 ADR：QED-Engine [ADR 0002](../../../docs/adr/0002-frontend-and-port-centralization.md)（跨仓库决策源）；本仓库 [ADR 0013](../adr/0013-selective-history-retention.md)、[ADR 0017](../adr/0017-consolidated-task-ledgers-and-roadmap.md)
关联设计：[document-pipeline.md](../design/document-pipeline.md)、[web-workbench.md](../design/web-workbench.md)；QED-Engine [service-contracts.md](../../../docs/design/service-contracts.md)、[dataset-conventions.md](../../../docs/design/dataset-conventions.md)、[configuration-and-secrets.md](../../../docs/design/configuration-and-secrets.md)
关联 Tracker：`docs/trackers/todo.md`（ALN-007；承接 ALN-002/003，登记 ALN-006）
归档判定：迁移完成且门禁通过后 Completed，按 ADR 0013 判定 Retain/Delete

## 目标与成功标准

承接 QED-Engine 教材下载轮（根仓库 ARCH-002）的 Axiom-Flow 对齐执行：端口、数据目录、配置与
数据库统一。

1. 端口迁移：API/Worker 由 8000 迁移到 **8902**；CORS 白名单、README、开发/运维指南、启动命令
   与冒烟脚本同步；迁移完成前保留 8000 侧 CORS 兼容（ADR 0002 约定）。
2. 数据目录：解析产物默认写入根 `dataset/axiom-flow/parsed/`（相对 QED-Engine 根仓库解析）；
   manifest / 页面事实 / 内容图片的目录布局沿用。
3. 配置统一：直读根 `.env` `QED_*` 变量（`QWEN_API_KEY`→`AXIOM_API_KEY`、`QED_OCR_MODEL`→
   `AXIOM_VISION_MODEL`、`QED_MODEL`→`AXIOM_KNOWLEDGE_MODEL`、`QED_DB_*`→`AXIOM_MYSQL_*`）；
   旧变量保留别名兼容；`load-env.ps1` 映射依赖退役。
4. 数据库统一（2026-08-04 用户裁决）：MySQL 8 新建 `qed` 库，`af_*` 表经 Alembic 初始化；存量
   `xqfm11` 库不迁移、不改名（存量迁移与归档另立 D 类计划）。
5. `web/` 工作台维持现状（ALN-004 后置，本轮不动）。

成功标准：8902 启动 API/Worker 并完成一次解析，产物落 `dataset/axiom-flow/parsed/`，qed 库
记录完整；无根 `.env` 时以默认值降级运行；QED-Engine 配置中心离线时解析链路可用。

## 范围与非目标

范围内：上述 1-4 项及相应契约测试与文档同步（design/architecture 实现状态、环境变量用例、
冒烟与降级用例）。

非目标：
- `web/` 工作台迁入根仓库统一前端（ALN-004，后续轮）。
- OCR 多后端 glm-ocr 适配（ALN-005，后续轮）。
- 读取 `dataset/qed-tracker/raw/` 的批量导入解析接口（ALN-006，Phase 2 前置登记）。
- 存量数据与 `xqfm11` 库迁移、正式发布与标签（D 类操作另立计划）。

## 前置条件

- 用户已确认（2026-08-04）：8902 端口、dataset 目录、`QED_*` 直读、qed 库统一。
- QED-Engine ADR 0002 与根仓库契约文档（service-contracts / dataset-conventions /
  configuration-and-secrets）已更新；ALN-001 登记计划完成，本计划为其确认后的执行入口。
- 根 `.env` 已含 `QED_DB_*` 且本机 MySQL 8 实例可连接（qed 库由实现轮初始化）。

## 工作项

1. 端口迁移（ALN-002）：CORS 白名单 8000→8902（保留 8000 兼容至前端迁移完成）、README、
   `docs/guides/development.md`、`docs/guides/operations.md`、启动命令与冒烟脚本。
2. 数据目录（ALN-003 部分）：`infrastructure/config.py` 默认 data_dir 指向根
   `dataset/axiom-flow/parsed/`，文档与测试同步。
3. 配置直读（ALN-003 部分）：`QED_*` 变量映射与别名兼容；`load-env.ps1` 映射依赖退役。
4. 数据库（ALN-003 部分）：qed 库初始化（建库 + `alembic upgrade head` 建立 `af_*` 表）；
   `xqfm11` 存量不动；MySQL 集成测试适配新库名。
5. 契约与文档同步：design/architecture 实现状态更新、环境变量用例、冒烟与降级用例。

## 验证与验收

- 全量门禁：`pytest tests -q`（含 contract）、ruff 无错误、`node --check web/app.js`。
- 8902 冒烟：启动 API/Worker，提交解析任务，产物落 `dataset/axiom-flow/parsed/`，qed 库记录
  完整（af_documents / af_artifacts 可见）。
- 降级：无根 `.env` 时服务以默认值启动并输出提醒；QED-Engine 配置中心离线时解析链路可用。
- 前端兼容：迁移完成前 8000 侧 CORS 兼容保留，Axiom-Flow `web/` 工作台继续可用。

## 回滚

- 变更在本仓库提交，回滚 = git revert + 恢复旧默认（端口 8000、自身 data/、`AXIOM_*` 变量）。
- `qed` 库为新建库（表可重建），回滚不触存量 `xqfm11`；不做任何存量数据迁移。

## 关闭与归档

- 工作项完成且门禁通过后转 Completed（关闭结果 Achieved）；ALN-002/003 从 todo 原子移除并写
  completed 台账；按 ADR 0013 判定 Retain/Delete。
