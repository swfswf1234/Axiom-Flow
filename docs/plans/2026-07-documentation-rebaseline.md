# 文档体系与 Agent 工作流重基线

状态：Completed  
最后更新：2026-07-27

## 目标

让新架构只有一个活跃入口，并使 Agent 能按可追溯、可验证的流程推进后续实现。

## 工作项

- [x] 归档 v0.1 MinerU 基线文档，保留原始内容。
- [x] 建立根 `AGENTS.md`、文档地图、架构、ADR 与追踪入口。
- [x] 建立解析、知识、Excel、质量与 Web 的目标设计。
- [x] 依据新设计重写根 README 的运行定位。
- [x] 确认文档链接、Git 变更和术语一致性。

## 验证

- `docs/README.md` 可定位到当前架构、设计、ADR、计划与任务。
- 历史资料不再位于活跃设计目录。
- 旧的 `docs/agents_read.md` 不再包含独立且过期的操作规则。

## 验证结果

2026-07-27 已检查全部本地 Markdown 链接可解析，并执行 `git diff --check`，未发现
空白错误。历史基线已移动到 `docs/history/2026-07-mineru-baseline/`。
