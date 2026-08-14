# ADR 0023：移除 GitHub Actions CI，本地门禁为唯一门禁

状态：Accepted
日期：2026-08-12
领域：工程治理
决策阶段：v0.3
取代：—
被取代：—

## 背景

本仓库 `.github/workflows/ci.yml` 提供 GitHub Actions 四阶段门禁（静态检查、unit+contract、
integration+system、smoke），只在直接推送 `main` 或创建 Pull Request 时运行。项目是单人本地
开发，本地关闭门禁（`pytest tests -q` + `ruff check src tests` + `node --check web/app.js`）已
完整覆盖同一检查面；远端 CI 不提供额外验证价值，还增加维护成本。根仓库 QED-Engine 亦无远端
CI（其 `docs/standards/testing.md` 规定提交前本地执行 `pytest tests -q` 与 `ruff check`）。

## 决定

1. 删除 `.github/workflows/ci.yml`（连同 `.github/` 目录），不再使用 GitHub Actions。
2. 本地门禁成为唯一门禁：推送 `release` 或合并回 `main` 前必须本地执行 `pytest tests -q`、
   `ruff check src tests` 并全绿；发布仍按 D 类计划执行完整差异和人工复核。
3. 同步更新 testing.md 门禁表述、README 徽标与技术栈、AGENTS/开发指南分支协作与命令、活跃
   计划验证节；已归档历史与 ADR 正文中的 CI 引用属于当时记录，不回溯改写。

## 后果

- 远端无任何自动验证：分支推送不等同于已验证，本地门禁是唯一验收依据。
- 消除 Actions 配置与徽标维护成本；单人流程更简单。
- 分层测试架构（ADR 0021）与公开 fixture 回归（ADR 0019）的检查面不变，仅执行场所从远端
  CI 改为本地命令。

## 关联

- 关联标准：`docs/standards/testing.md`（门禁表述同步，本 ADR 为「门禁必需阶段」变更依据）
- 关联 ADR：[0021](0021-layered-deterministic-test-architecture.md)（分层测试架构，执行场所
  改为本地）
- 关联计划：`docs/plans/2026-07-v03-engineering-baseline.md`（发布验证节改写）
- 关联指南：`docs/guides/development.md`（分支协作与覆盖率命令）