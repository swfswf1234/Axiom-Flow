# 目录行层级与身份确定性后置＋既有行改判腿（toc-shape-postpass-and-rejudge）

状态：Accepted
任务类型：B
最后更新：2026-11-12
关联 ADR：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、管线归本仓）、
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪单源）、
[ADR 0018](../adr/0018-task-column-bound-and-progress-ban.md)（列宽与进展禁写）
关联设计：[knowledge-analysis.md](../design/knowledge-analysis.md)（`KA-35` 目录正腿的层级口径、
`KA-39` 身份位、`KA-62` 三级细目折入）、[api.md](../architecture/api.md)（`kp` 路由与响应形状）
关联 Tracker：docs/trackers/todo.md（PLAN-035 镜像行；请求来源＝QED-Engine 根仓库 REQ-164）
归档判定：待定（重跑读数与回归交付后按 `qed-closeout` 判 Retain/Delete；条文并入 `KA-35`／`KA-39`）

**执行分工**：需求方＝QED-Engine 根仓库；目标项目＝Axiom-Flow；接口面＝`af_kp` 目录行成行后置
（`depth`／`meta.toc_role`／`meta.toc_kind`）与既有行改判腿（`PATCH /api/v1/kp/nodes/{node_id}`
扩字段或等价端点，最终形状由本仓定）；评审方＝用户；执行方＝Axiom-Flow；验收标准＝本文件
「验证与验收」节与台账行 PLAN-035 的 S 条目。

## 目标与成功标准

**目标**：目录腿的模型整理行不再原样入库——`level` 与身份位在成行前过一遍确定性后置，让
`§N` 行按书序归节、节下纯编号细目按 `KA-62` 折入、书级附件按名字标 `reference`；并给已在册的
错判行一条就地改判的腿，使 b14 这类历史数据无需整本重解析即可修正。

1. **S1 层级后置**：模型整理行的 `level` 再过 `normalize_entry_levels`（与确定性条目腿同口径）；
   b14 重跑后「§3. 函数的三角展开式的唯一性」为 `depth=2` 且父＝「二十章 傅里叶级数 (续)」，
   「746.～751.」六条纯编号细目零行，`result.folded_sub_items` 覆盖 6 条。
2. **S2 身份兜底**：`llm:toc` 行在模型未给身份位时按名字集合派生（练习题标签优先、编号起头
   豁免；含子目的附录也归附件，`KA-68` 的结构第二趟只兜「无内容子行」）；b14「附录 极限的一般
   观点」为 `toc_role=reference`／`toc_kind=appendix`，「索 索引」仍 `reference`／`index`，`§N`
   行不被误判成附件。
3. **S3 改判腿**：`confirmed=0` 的目录行可就地改判 `depth`／`toc_role`／`toc_kind`（父子重挂
   同时生效），本应折入的纯编号子行置 `rejected` 不物理删；`confirmed=1` 行改判请求被拒并点名；
   不触发整本重解析、零 ALTER。
4. **S4 回归**：b05／b12／b13／b18 目录行集合、身份位与批次账本键集与改造前逐行相等；b14
   屏上 §3 为知识点、附录为其他；本仓门禁全绿，根侧读时投影在新形状下成为恒等。

成功标准条目化判据的正本在 `docs/trackers/todo.md` 的 PLAN-035 行，本壳只承接拆解与证据。

## 范围与非目标

**范围**：`_cleaned_model_entries` 之后的确定性后置接线、`_outline_identity` 的模型行身份兜底、
既有行的改判写入腿、`KA-35`／`KA-39` 条文与 `api.md` 随步、单测与 b14 重跑读数。

**非目标**：不改提示词、不增加模型调用次数、不改点腿／内容腿／批次键集；不做多语言模板；
不物理删行、不加列（`depth` 与 `meta` 两键现状即可承载）；根侧的读时投影与共享表清理归
QED-Engine，不在本仓实施。

## 前置条件

1. 目录腿正腿与回落腿、`fold_numbered_sub_items`（`KA-62`）与身份位（`KA-39`）已落地（PLAN-025／
   PLAN-031／PLAN-032 在册）。
2. b14（`mathanalysis-b14`，微积分学教程 Vol.3）已有目录批读数；根侧请求稿：
   QED-Engine `docs/plans/2026-11-12-af-toc-shape-postpass-and-rejudge-request.md`。
3. 零 DDL：`af_kp.depth` 列与 `meta.toc_role`／`meta.toc_kind` 两键现状可承载；改判腿不扩
   `gen_status` 闭集（作废沿用 `rejected`）。
4. 新增文档须同步本仓文档白名单与 `pytest tests/contract`。

## 接口契约（拟）

```
PATCH /api/v1/kp/nodes/{node_id}          # 既有端点扩字段（首选）或等价的一次性重判端点
request  {"depth": 1|2?, "parent_id": str?, "toc_role": "knowledge"|"reference"?,
          "toc_kind": ""|"front_matter"|"back_matter"|"symbol_table"|"appendix"
                      |"bibliography"|"index"|"exercise"?, "if_rev": int?}
成功     200 行回显（与工作区同形）
拒绝     404 行不存在 / 409 if_rev 冲突 / 422 confirmed=1 行不可改判 / 422 取值越界
```

说明：`depth` 与 `toc_role`／`toc_kind` 只对 `node_kind='outline'` 且 `confirmed=0` 的行开放；
`depth=2` 时 `parent_id` 必填且须指向同书 `depth=1` 的行；`toc_role`／`toc_kind` 落 `meta`，
不进独立列。若本仓判断「整批重判」比逐行 PATCH 更稳，可用一条重判端点替代，回执写明最终形状
与调用次数上界。

## 工作项

| 条目 | 内容 | 状态 |
| --- | --- | --- |
| W1 | **demo 门**＝b14 工作区现状读数复核（§3 一级章、746~751 二级行、附录 chapter）后，用临时脚本调用新改判腿修正一条行并回读，到门暂停待人工验收 | 未开始 |
| W2 | 层级后置：模型整理行成行前过 `normalize_entry_levels`，`§N` 行归前一章；单测覆盖 b14 形与「模型二级行为不动」 | 未开始 |
| W3 | 身份兜底：`_outline_identity` 的 `llm:toc` 分支按名字集合派生 `reference`＋`toc_kind`（练习优先、编号起头豁免）；单测覆盖附录／索引／§N 三形 | 未开始 |
| W4 | 改判腿：`KpNodePatch` 扩 `depth`／`toc_role`／`toc_kind`（或重判端点），`confirmed=1` 拒绝；子行作废入 `rejected`；`api.md` 与 `KA-35`／`KA-39` 随步 | 未开始 |
| W5 | 重跑与回归：b14 目录腿重跑取 §3／746–751／ appendix／索引读数；b05／b12／b13／b18 行集合与批次键集逐行对拍；`pytest tests -q`＋`ruff check` | 未开始 |

## 过程与证据

- 2026-11-12｜请求来源：QED-Engine 根仓库 REQ-164（用户真机裁定 b14 两例错判）。根侧只读证据
  已随请求稿附：b14 原始读数 `afkp-d685914b98c8`（§3 一级章、page 519）、
  `afkp-40c9c30bdf27`～`afkp-e768cf93ba01`（746~751 二级行、父＝§3）、
  `afkp-58bc2d5a6665`（附录一级章、page 534）、`afkp-3159f8b489af`（索引已 `other`）；
  代码行号 `orchestrator/knowledge.py:1246`／`1514`、`normalize/toc.py:207／224／460`。
- 2026-11-12｜承接状态：待本仓排期；根侧读时投影为临时兜底，本仓落地后成为恒等（根侧不改代码）。
- 2026-11-12｜**与 `KA-68` 的边界**：其结构第二趟只把「没有内容子行」的一级知识行降 `other`；
  b14 附录带 752–762 十一枚知识点子行，因此仍是 `chapter`。本计划 S2 补的是**按名字**的附件判
  （含子目的附录也归 `reference`），并**不改** `KA-68` 的结构判据；两趟判序＝先结构降 `other`、
  再按名字补 `reference`，同判为附件时不重复计数。
- 2026-11-12｜**内容切分核验（根侧已做，结论＝切分无错）**：b14 细目行页区间逐行连续且与题名
  对应（746 519–522 … 751 531–533；752 534–535 … 762 548–550），§3 与附录的父行总结已综合其
  全部子目。因此 W5 重跑只为修正层级／身份，不因「正文切错」重写总结；但改判后父行
  `page_start/page_end` 只落首页（§3＝519、附录＝534）需随之重算为子目区间的最小／最大页，
  否则详情面读源只显示首页——请在 W4 改判腿或 W5 重跑回执里写明这一项的落点。

## 验证与验收

- **单测**：层级后置（b14 §3 形、模型已给二级不动）、身份兜底（附录／索引／§N 三形）、改判腿
  （`confirmed=0` 可改、`confirmed=1` 拒、取值越界 422、子行入 `rejected`）。
- **真机**：b14 重跑目录腿后 `§3` 二级且父正确、746–751 零行、`folded_sub_items=6`、附录
  `reference/appendix`；b05／b12／b13／b18 目录行集合与批次键集逐行相等。
- **门禁**：本仓 `pytest tests -q` 与 `ruff check` 全绿并留输出；`pytest tests/contract` 对 `api.md`
  与 `KA-35`／`KA-39` 的断言同批更新。
- **回执**：向 QED-Engine 回执提交号、b14 读数与门禁输出，写入根侧承载壳的过程与证据节。

## 回滚

- 撤回后置与改判腿（单提交）即回旧形状；已在册行不被本次改动改写（改判是显式写入），
  根侧读时投影继续兜底，8903 行为不回退。
- 零 DDL、零迁移；`rejected` 行只作废不物理删，无需数据清理。

## 关闭与归档

- S1~S4 逐条有真机读数与门禁输出后关闭；条文并入 `KA-35`／`KA-39`，计划按 `qed-closeout`
  判 Retain/Delete；根侧回执随 REQ-164 收口。
