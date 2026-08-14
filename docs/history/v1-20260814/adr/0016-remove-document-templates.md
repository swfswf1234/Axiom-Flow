# ADR 0016：删除文档模板目录

状态：Accepted
日期：2026-07-29
领域：工程治理
决策阶段：v0.3
取代：—
被取代：—

## 背景

`docs/templates/` 保存 ADR、Design、Plan 和 Experiment 四个 Markdown 骨架。ADR 与 Plan 模板
复制了 standards 已定义的字段和章节；Design 模板与当前按工作流划分的设计正文并不完全一致；
Experiment 模板则没有对应生命周期，真实实验已经由 `evaluation/manifests/*.template.json`、
评测报告和代码校验承接。继续保留会形成低优先级的第二份格式规则，并可能误导新文档创建。

## 决定

1. 删除整个 `docs/templates/` 及其文档分类，不保留空目录或 History 副本。旧模板没有唯一事实，
   需要时从 Git 锚点 `f2e8a2a` 恢复。
2. 新 ADR、Design 和 Plan 必须先满足对应 standard；可参考最近一份仍有效的同类文档组织内容，
   但必须重新确认编号、状态、关联、范围和项目事实，不能复制旧结论。
3. ADR 0016 及后续 ADR 必须包含背景、决定、后果和关联四个有序章节，由 ADR 治理测试前向守护；
   旧 Accepted ADR 不因模板删除而追溯改写。
4. 实验继续使用 `evaluation/` 下的 JSON manifest、结果与报告契约，不新增 Markdown 实验模板。

## 后果

文档目录减少一个仅保存派生骨架的类别，standards 与真实文档成为创建和校验入口。新建文档比
复制模板多一步选择同类参考，但不会再从过时骨架引入错误字段。自动门禁直接检查实际 ADR、Plan
和 Design，避免只验证模板而不验证产物。

## 关联

关联 [ADR 0015](0015-standards-as-governance-source.md)、[文档规范](../standards/documentation.md)、
[ADR 治理](../standards/adr-governance.md)和[评测治理](../design/evaluation-governance.md)。
