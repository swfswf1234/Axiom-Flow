# 文档与代码双向追溯机制

状态：Completed  
最后更新：2026-07-27

## 目标

让人工和 Agent 能从代码反查设计、从设计定位代码和测试，并把映射一致性作为任务关闭门禁。

## 工作项

- [x] 以 `docs/architecture/code-map.md` 建立唯一映射事实源。
- [x] 将现有 v0.1 主链标为 Legacy，并只关联历史基线。
- [x] 为 Current 评测代码、测试和 Legacy 模块补中文文件头与 DesignRef。
- [x] 为活跃架构和设计文档补齐关联代码、测试和 ADR 元数据。
- [x] 增加自动映射一致性测试并执行全量测试。

## 验证结果

2026-07-27 运行 `python -m pytest -q`，12 项测试通过；本地 Markdown 链接检查通过，
`git diff --check` 未发现空白错误。
