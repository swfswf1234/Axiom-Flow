# 课程级章总结与导读生成腿（merge-chapter-text-generation）

状态：Accepted
任务类型：B
最后更新：2026-11-10
关联 ADR：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、管线归本仓）、
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪单源）
关联设计：[knowledge-analysis.md](../design/knowledge-analysis.md)（`KA-43` 点腿与节级总结、`KA-45` 章腿两段、
`KA-48` 单点重生成、`KA-57` 详情提示词冻结）、[api.md](../architecture/api.md)（`kp` 路由与响应形状）、
[model-integration.md](../design/model-integration.md)（8900 文字网关与槽位）
关联 Tracker：docs/trackers/todo.md（PLAN-034 镜像行登记在普通任务轮；请求来源＝QED-Engine 根仓库 REQ-163）
归档判定：待定（实现与联调读数交付后按 `qed-closeout` 判 Retain/Delete；条文晋升 `KA-45` 与新条文
「证据驱动章腿」）

## 目标与成功标准

**目标**：把本仓 PLAN-033 落地的章腿（章总结＋三段导读）从「只读本书工作区材料」扩展出一条
**无状态、证据驱动**的通路：调用方（QED-Engine 8900）传入合并后的多本证据，本端点用同一套提示词、
章级检索与确定性校验生成课程级章正文并直接返回，不读写 `af_kp`、不落库、零 DDL。这样课程级
总结与导读只在 8902 实现一处，根侧只组装证据与落库。

1. **S1 端点形状**：`POST /api/v1/kp/merge/chapter-text` 接受证据包（`course_id`、`chapter`、
   `sources[]`），返回 `{summary, narrative, check:{passed, issues[], model}}`；无状态、零写库。
2. **S2 口径复用**：提示词、章级检索（中文维基优先→英文维基→数学专业源，≤8 条、限长去重、
   fail-open）、确定性校验与同模型一次重试全部复用 PLAN-033 的 `C-1`／`C-2`／`C-4`／`C-5`／`C-6`。
3. **S3 失败语义**：证据缺块 422 并点名；文字槽位不可用 503；两次校验不过 → 200 照写并
   `check.passed=false` 带 `issues` 与模型名。
4. **S4 归因**：每笔调用一行 `qed_llm_calls`，`step` 可指认本腿；不点名模型、不切换共享槽位。
5. **S5 回归**：既有章腿／点腿、批次账本、`stage` 四值与端点面逐字不变；默认门禁零出站。

成功标准条目化判据的正本在 `docs/trackers/todo.md` 的 PLAN-034 行，本壳只承接拆解与证据。

## 范围与非目标

**范围**：新端点路由与请求／响应模型、证据包到章腿材料的映射、检索与校验的重用接线、
`qed_llm_calls` 归因、单测与契约、`api.md` 与 `KA-45` 条文随步。

**非目标**：不改 PLAN-033 的提示词本体与检索源；不做点级检索；不写 `af_kp`、不建表、不改批次；
不做多语言模板；根侧的落库、`confirmed` 保护与幂等由 QED-Engine 侧承载。

## 前置条件

1. PLAN-033 的 W2~W6 已落地（两条提示词、章级检索包、模型名只读探测、校验与重试链）。
2. 8900 文字网关可达且当前生效槽位可用；出站唯一通道不变。
3. 调用方证据包字段已与本计划「接口契约」节对齐（根侧请求稿：QED-Engine
   `docs/plans/2026-11-10-af-merge-chapter-text-generation-request.md`）。
4. 零 DDL、零新表；端点不持久化请求与结果。
5. 新增文档须同步本仓文档白名单与 `pytest tests/contract`。

## 接口契约（拟）

```
POST /api/v1/kp/merge/chapter-text
request  {
  "course_id": str,
  "chapter": {"name": str, "summary": str?, "narrative": str?},
  "sources": [{"book_id": str, "title": str?, "chapter_name": str,
               "summary": str?,
               "points": [{"name": str, "summary": str?}]}]
}
response {
  "summary": str,                 # 课程级一段话（建议 ≤1000 字）
  "narrative": str,               # 三段导读（总问题→推进→能力）
  "check": {"passed": bool, "issues": [str], "model": str}
}
错误：422 证据缺块（`detail` 点名）；503 文字槽位不可用；其余按既有错误面。
```

映射：`chapter` 与 `sources[]` 按章腿材料的三块（章前材料／子点总结／检索资料）组装，检索只由
本章名与 `sources[].points[].name` 派生；证据为空时按「无资料」照常生成。

## 工作项

| 条目 | 内容 | 状态 |
| --- | --- | --- |
| W1 | **demo 门**＝用现成真证据（b05 第 3 章材料＋b18 同章材料）调端点一次，回读 `summary`／`narrative`／`check`，并由根侧替身桩按 8900 消费形状解析；到门暂停待人工验收 | 未开始 |
| W2 | 证据包模型与映射：请求／响应 Pydantic 模型、证据到章腿材料的组装、空证据与缺块 422 | 未开始 |
| W3 | 生成接线：复用 PLAN-033 的章腿提示词、检索包、校验与同模型重试；`check` 组装；账本 `step` 归因 | 未开始 |
| W4 | 失败面与超时：503 槽位不可用透传、超时上限与「不半写」；单测覆盖 422／503／照写三形态 | 未开始 |
| W5 | 条文与门禁：`api.md` 路由与响应形状、`KA-45` 补「证据驱动章腿」、文档白名单；`pytest tests -q`＋`ruff check` | 未开始 |

## 验证与验收

- 单测：形状与映射、空证据、缺块 422、槽位不可用 503、两次校验不过照写（`check.passed=false`）、
  检索 fail-open；默认门禁以替身实现，不出站。
- 契约：`openapi.json` 出现新路由且与 `api.md` 一致；既有 `kp` 路由逐条不变。
- 联调：根侧 REQ-163 W4 用 b18＋b05 真证据走通一次，读数（`check`、账本行、耗时，`summary`
  字数）回填本计划「目标与成功标准」节。
- 回归：`full`／`outline`／`content` 三值与点腿调用数逐字不变；`af_kp` 行数与批次账本零变化。

## 回滚

删除新路由与实现（单提交）即可；无迁移、无表、无落盘。已有章腿与批次不受影响。

## 关闭与归档

W1 验收通过、W2~W5 证据齐、根侧回执到达后按 `qed-closeout` 收口：结论写壳、PLAN-034 行与
REQ-163 行同步关闭、`api.md` 与 `KA-45` 条文晋升；归档判定按 Retain/Delete 两态判定。
