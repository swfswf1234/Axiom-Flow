# 公开解析回归基线

状态：Completed
任务类型：B
最后更新：2026-07-29
关联 ADR：`docs/adr/0019-public-fixture-and-private-benchmark-boundary.md`
关联设计：`docs/design/evaluation-governance.md`
关联 Tracker：`docs/trackers/todo.md`（QA-001）
归档判定：Retain；首个公开解析基线保留实现范围、验收和回滚证据。

## 目标与成功标准

建立可提交 GitHub 的自制数学 PDF、人工金标和无外部调用的完整事实比较器。成功标准是 fixture
许可与哈希完整，生产产物契约可确定性回放，所有质量维度及反例测试通过。

## 范围与非目标

范围包括 `evaluation/` 目录治理、公开 fixture、私有 benchmark 边界、CLI、测试和文档映射。
不采纳任何真实模型路线，不把 replay 结果解释为 OCR 质量，也不提交 Rudin 内容。

## 前置条件

- ADR 0019 已接受。
- 公开样本由项目自行创作并声明许可。
- 当前 schema v2 解析产物仍是 fixture 的输出契约。

## 工作项

- [x] 重组公开 fixture、私有 benchmark 和脱敏报告目录。
- [x] 创建四页数学 PDF、来源说明、许可、回放输入和人工金标。
- [x] 实现完整事实比较器及稳定退出码。
- [x] 增加正确结果和逐类篡改回归测试。
- [x] 同步设计、代码映射、指南和 CI 关闭门禁。

## 验证与验收

执行评测专项测试、文档映射测试、Ruff、全量 Pytest 和 `git diff --check`。使用 CLI 对仓库 fixture
执行一次成功比较，并确认缺失文本、公式、表格、图片、bbox 或哈希时返回失败。

## 回滚

删除新增 fixture 和比较器，恢复原评测模块路径及文档映射。公开 fixture 不写生产数据，回滚不需要
迁移数据库或清理 MySQL。

## 关闭与归档

关闭结果：Achieved

公开 fixture 自校验通过，Ruff 与全量 104 tests 通过。计划从 todo 原子迁移到 completed，并按
归档判定移入 `docs/history/plans/2026-07/`。
