# v0.3 Rudin 20 页 OCR 真实试跑

状态：Completed
任务类型：D
关闭结果：Rejected
最后更新：2026-07-29
关联 ADR：`docs/adr/0010-qwen-ocr-only-rudin-trial.md`、`docs/adr/0011-current-parse-run-and-prunable-artifacts.md`
关联设计：`docs/design/document-pipeline.md`、`docs/design/evaluation-governance.md`
关联 Tracker：`docs/trackers/todo.md`（EXP-002）
归档判定：Retain；保留真实外部调用、运行数据选择与清理、manifest 哈希和人工验收结论。

## 目标与成功标准

依据 ADR 0010，对 Rudin PDF 物理页 20–39 执行 `qwen-vl-ocr` 单模型真实试跑，验证固定产物链路，
并以五页严格人工抽检决定该路线能否进入 317 页整书解析。成功标准为 20 页产物完整且五页抽检
全部通过；链路完成但质量门禁未通过，因此路线结论为 Rejected。

## 范围与非目标

本计划只记录已批准方案的 20 页 D 类真实执行、产物校验、当前运行选择和旧运行清理。扫描教材
候选评测、确定性实现分别由既有报告、ADR 和代码提交承接；317 页整书、知识抽取、工作簿与知识
发布不在本计划范围。

## 前置条件

ADR 0010 已冻结 PDF 第 20–39 页、`qwen-vl-ocr`、每页最多 3 次和总计 60 次调用；解析、不可变
产物、当前运行选择和受保护清理已经通过确定性测试。

## 工作项

- [x] 使用 `qwen-ocr-markdown-v2` 契约完成 PDF 第 20–39 页真实调用。
- [x] 校验页面文字、blocks、页图、规范化 JSON、原始响应、相对路径和 SHA-256 清单。
- [x] 严格抽检 PDF 第 20、24、29、34、39 页并形成评测报告。
- [x] 显式选择最终 v2 为当前 ParseRun，将旧失败运行和 v1 运行清理为 `pruned` 摘要墓碑。
- [x] 完成 Ruff、全量 Pytest、代码映射、Markdown 链接和差异门禁。

## 验证与验收

最终运行使用 22 次调用完成 20 页，81 个派生文件复算哈希一致，manifest SHA-256 为
`b0f44ed0d9c6a5295773c5b429259e330ea6a3622596b5380361308305d1de35`。人工抽检结果为 3/5：
第 20、24 页存在主要公式变量误识别，第 29、34、39 页通过。完整证据见
[20 页链路试跑报告](../../../../evaluation/benchmarks/private/rudin/reports/rudin-qwen-ocr-20-v1.md)。

## 回滚

真实运行采用追加式记录，不覆盖原 PDF。最终 v2 已显式设为当前运行；四个旧运行仅保留可审计
`pruned` 墓碑，暂存目录已在复核后清空。该清理结果不能仅靠文件移动回滚，恢复需使用执行前
数据库备份和 Git 锚点 `a6ec4e0` 对应的操作记录。

## 关闭与归档

工程链路和产物完整性已达到执行目标，但五页质量门禁仅通过 3/5，关闭结果为 Rejected，不启动
317 页整书解析。后续候选保留在 todo，冻结假设、样本、预算和采纳门槛后建立新的 C 类计划，
不得恢复或改写本计划。
