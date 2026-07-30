# 本地运行数据与构建产物重置

状态：Completed
任务类型：D
最后更新：2026-07-29
关联 ADR：`docs/adr/0019-public-fixture-and-private-benchmark-boundary.md`
关联设计：`docs/architecture/data-lifecycle.md`、`docs/design/evaluation-governance.md`
关联 Tracker：`docs/trackers/todo.md`（DATA-001）
归档判定：Retain；数据删除操作保留目标、审计摘要和恢复边界。

## 目标与成功标准

审计后删除仓库内被忽略的 `data/`、临时 `build/` 和空 `scripts/`，验证应用仍能按需重建运行目录。
成功标准是精确目标已清理、外部 Rudin 原件仍可验证、MySQL 未修改且测试使用临时目录通过。

## 范围与非目标

只处理 `E:\qed\Axiom-Flow` 下三个精确目录。不得删除 `E:\qed\dataset` 中的原件，不重建、迁移、
清空或直接修改 MySQL；不把本地教材、页图、响应或审计明细提交 Git。

## 前置条件

- 公开 fixture 已落地，评测不再依赖旧 `data/` 内容。
- Rudin 外部原件存在且 SHA-256 与冻结 manifest 一致。
- 需要长期保留的结论已经进入 ADR、脱敏报告或 Git 历史。

## 工作项

- [x] 只读统计目标文件数量、大小和分类，不保存敏感路径清单。
- [x] 验证外部 Rudin 原件和冻结哈希。
- [x] 精确删除 `data/`、`build/` 和空 `scripts/`。
- [x] 验证配置、测试和应用路径可按需创建新 `data/` 子目录。
- [x] 复核 Git 差异和数据库未进入操作范围。

清理前统计为 `data/` 115 个文件、27,029,158 字节，`build/` 37 个文件、181,368 字节，
`scripts/` 为空。外部 Rudin SHA-256 为
`341544f3fa9ce6ac8bf3860b4d9f9e4e86b1d2778e2a8644c1cafb31267ed968`，与冻结 manifest 一致。
运行库未修改，但只读检查发现旧文件引用，已登记 DATA-002，不能据此发布。

## 验证与验收

记录清理前后的目录存在性与摘要；执行文件定位、解析产物、评测和全量测试。清理后仓库不包含
Rudin PDF、页面截图、完整响应或打包副产物。

## 回滚

`build/` 可由打包命令重新生成，运行 `data/` 由应用重新创建。旧运行数据不在仓库内恢复；需要的
Rudin 实验必须从外部原件重新执行。MySQL 未修改，因此不需要数据库回滚。

## 关闭与归档

关闭结果：Achieved

三个精确目标均已删除，临时目录回放和全量 104 tests 证明路径可按需重建；运行库未修改，已以
DATA-002 登记其旧文件引用。计划从 todo 原子迁移到 completed，并按归档判定移入
`docs/history/plans/2026-07/`。
