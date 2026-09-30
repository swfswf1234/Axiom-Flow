# ADR 0014：开发分支改名 `develop`，`release` 名称退役

状态：Accepted
最后更新：2026-09-30
关联代码：无（分支口径与治理文档措辞，不映射具体模块；`src/` 与 `scripts/` 零分支名硬编码）
关联测试：无（分支名不在契约断言面内；改动为文档措辞，由全量门禁与链接守护承接）

## 背景

本仓库分支模型只有两条：`main` 保存已确认的稳定基线，另一条承接日常开发。后者自 v2 纪元起名为
`release`，与本仓库既有用词冲突：

1. **与「发布」动作抢词**：`AGENTS.md` 与 [任务生命周期](../standards/task-lifecycle.md) 的 D 类定义、
   「不得隐式创建标签或 GitHub Release」条款都用 release 一词表达**发布动作**，而 `release` 分支本身
   不表示任何发布动作——日常提交全部落它，名字与实际职责相反。
2. **与 v0.1 发布口径混读**：v0.1 收口时「打标签 `v0.1.0` 并把 `release` 合入 `main`」（见
   `project-status.md` 发布口径段）——分支名与版本通道写在同一句里，读者难以判断 `release` 是通道还是
   动作。
3. **名称改动的成本此刻最低**：本仓库无远端 CI、无托管流水线（`.github/` 不存在），活跃文档中的
   `release` 字样只落在三份治理文档，其余全属历史与当时记录。

用户 2026-09-30 裁决：开发分支改名为 `develop`，分支职责与合并纪律不变。

## 决定

1. **开发分支名固定为 `develop`**：日常实现的提交与推送进 `develop`；`main` 只接收用户显式要求、
   且完成本地门禁后的 `develop` 合并。本仓库除这两条分支外不设其他长期分支，不引入 feature/hotfix
   分层。
2. **纪律不变，只换名字**：合并回 `main` 仍需「用户明确要求 ＋ 适用门禁完成」两个条件，仍不得隐式
   创建标签或 GitHub Release；本仓库无远端 CI，本地门禁（`pytest tests -q` ＋ `ruff check src tests`）
   仍是唯一门禁，直接推送开发分支或合并回 `main` 前必须完成。
3. **同步面**：`AGENTS.md`「分支与完成门禁」、[测试架构与门禁](../standards/testing.md) 的门禁前置、
   [文档治理规范](../standards/doc-governance.md)「版本末期文档整理」触发条件三处正文改指 `develop`。
4. **历史措辞不回改**：`docs/history/**`、`completed.md` 已关闭行、`project-status.md` 的日期化轮次记录
   与已归档计划壳中的 `release` 字样属**当时记录**，保留不改写；本 ADR 是两者的对照点
   （2026-09-30 之前的 `release` ＝之后的 `develop`）。

## 后果

- **正面**：分支名不再与发布动作抢词；「哪条分支可以日常提交」只剩一个答案，`release` 一词在本仓库
  回归「发布」本义。
- **代价**：本地与远端各需一次改名，外部脚本或文档若硬编码旧分支名会指空。缓解＝改动前以
  `rg -i release` 全仓核对，确认命中只落在治理文档措辞与历史资料（实测如此，`src/`、`tests/`、
  `scripts/`、`pyproject.toml` 零命中）。
- **代价**：历史文档与新文档并存两种分支名，跨期阅读者需要知道二者等价——该说明只写在本 ADR，
  不逐处加注（避免把当时记录改写成解释文）。

## 取代

取代：无（本 ADR 只改 `AGENTS.md`、`standards/testing.md`、`standards/doc-governance.md` 三处的
**分支名称表述**，不改其门禁、合并纪律与 D 类发布定义）。
被取代：无。
关联：[ADR 0008](0008-fixed-doc-form-and-metadata-genres.md)（固定文档正文用词纪律）、v1 纪元归档决策
`0023-remove-github-actions-ci`（无远端 CI 的结论继续适用，只读参照
[历史 ADR 目录](../history/v1-20260814/adr/index.md)）。
