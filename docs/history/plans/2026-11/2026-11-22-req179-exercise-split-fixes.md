# REQ-179 习题切分四缺陷修复（图片 URL、占位解答、题面串位、解答边界）

状态：Completed
任务类型：B
最后更新：2026-11-25
关联 ADR：[ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0011](../../../adr/0011-ledger-one-row-per-task.md)（一任务一行）、[ADR 0010](../../../adr/0010-page-regions-are-parse-inputs.md)（产物正本唯一、版本目录布局——R1 图片端点在该布局内开读腿）
关联设计：`docs/design/knowledge-analysis.md`（`KA-53`/`KA-56`/`KA-70` 判据正本）、`docs/design/parsing-pipeline.md`（产物布局）；根侧请求稿（只读）：QED-Engine `docs/plans/2026-11-22-af-exercise-split-and-image-url-request.md`
关联 Tracker：docs/trackers/completed.md 的 PLAN-037 关闭行（并入根侧 REQ-179 承接线）
归档判定：Retain（b07 八章重跑、全量替换、同口径 S5 对比与界面验证为本仓独有且尚未提交的验收证据，不删除）

## 目标与成功标准

**目标**：修复根侧 2026-11-22 在 b07 实测查出的四条数据面缺陷，使习题集题包「图片可显示、
题面不串位、解答不被截断或吞邻题、失败不伪装成正文」。R1~R4 全部在本仓修复（用户 2026-11-22
裁决「优化腿给 AXIOM-FLOW」；R1 取**写入面**、接受 R4 需重跑生效、PLAN 编号取 037，均经用户当日裁决）。

**成功标准**（对齐根侧壳 S1~S5，本仓执行面）：

- S1 对根侧 R1~R4 逐条「落地」回执并附排期（登记于本壳「回执」节）。
- S2 题包内图片引用改写为可代理取的 URL（写入面），8902 提供内嵌图片读取端点，8900 字节转发即通。
- S3 判定失败的题包不再把「未配到解答」占位句固化成正文，改用**显式不可用标记**（与「真无解答」可区分）。
- S4 `split_exercise_items` 不再把节内正文枚举号收成假题（R3 串位形态消除，以真机取证定位后收紧候选面）。
- S5 题腿补「解答结束行号」契约（`solution_end`），`_slice_exercise_text` 双端切分；b07 重跑后
  截断+越界两类从 318 笔显著下降（读数回填本壳）。

## 范围与非目标

**范围**：`normalize/exercise.py`（题号候选面）、`engines/kp_gateway.py`（题腿 prompt 与双端切片）、
`engines/base.py`（题腿回复契约）、`orchestrator/knowledge.py`（题包组装与不可用标记）、
`api/main.py`（内嵌图片读取端点）+ `docs/architecture/api.md` 端点总表与小节**同批**晋升
（端点对账守护约束）、相应单测与契约测试。

**非目标**：

- 不改根侧前端渲染与契约（`MarkdownView` 图片分支归根侧另批）；
- 不改教材线判据（b05/b12/b13/b18 目录行与题包逐行不变）；
- 不重跑 b11（用户令：暂缓）；b07 重跑在四条修复落码并门禁全绿后由用户发令执行。

## 前置条件

1. 根侧已完成 b07 审核补跑全量（`failed` 662→2），四条缺陷清单出自该批实测读数（根侧壳取证节）。
2. 本机 `.env` 指向 `qed_test`；8902 可起。
3. R3 修复前必须先做真机取证：读 b07 实际题包与源页 markdown，确认假题号被收的形态再收紧
   （禁止凭猜测改题号闭集）。
4. R4 契约变更需重跑题腿才见效（用户已确认接受该排期）。

## 工作项

| # | 工作项 | 状态 |
| --- | --- | --- |
| W1 | 立项登记：本壳 + todo 镜像行 + 对根侧 R1~R4 回执 | 已完成（本壳即回执载体，见下节） |
| W2（R1） | 写入面图片 URL：题包组装时把 `pages/images/<sha>.jpg` 改写为 `/api/v1/books/{id}/images/<sha>.jpg`（与页图读腿同口径的根相对路径，8900 字节转发，前端按 API_BASE 取）＋ 8902 新增 `GET /books/{id}/images/{filename}` 端点（作用域内 `pages/images/` 读取）＋ api.md 同批晋升 | 已完成（`rewrite_pack_image_urls` 纯函数 + 独立/配套两写入点接线；端点 `get_pack_image` 单段文件名 + `ensure_within` 防穿越 + 扩展名媒体白名单；api.md 总表与小节同批晋升） |
| W3（R2） | 显式不可用标记：判定失败路径的题包不再以「未配到解答」占位句冒充解答正文，改写显式不可用标记（`exercise_pack_status` 同步识别，状态仍 `problem_only` 族） | 已完成（`EXERCISE_UNAVAILABLE_TEXT`；独立模式「模型未回草稿」与配套模式「本章解答读取失败」两路径落标记；占位句保留给「真没配到」；`exercise_pack_status` 两串都按 problem_only 读） |
| W4（R3） | 题号候选面收紧：先真机取证（b07 串位题包的源页 markdown），据实收紧 `split_exercise_items` 受理条件 | 已完成（真机取证＝`afkp-76774787c71d` 锚 snippet 证实串位题 `(2)` 是教材「下确界定义」的枚举小问；修复＝`_match_label` 分族（bracket 与裸括号分开）＋ `_dominant_family`（`【N】`/`Problem` 强形状锁族）；裸 `(N)` 自成节的排法不受影响） |
| W5（R4） | 解答结束边界：prompt 增 `solution_end`（解答最后一行的行号；判不出给 -1 回退段尾）、`_slice_exercise_text` 双端切分（end 缺失/非法回退现状）、`KA-56` 条文随步 | 已完成（prompt 双行号契约；切片行为表＝合法 end 双端切 / `-1`·非正·越界·非整数回退段尾 / `1≤end<start` 矛盾读数按空不猜；直证探针 `tmp/probe_slice.py` 全对；KA-56 条文随步另行登记） |
| W6 | 门禁与回执：全量门禁 + 契约 + 本壳回填读数；根侧回执成文 | 已完成（ruff 全绿 + pytest 1729 passed / 18 skipped，净增 14 例守护；契约 337 全绿含端点对账） |
| W7 | b07 重跑验收（用户已预授权机时，按节发令）：S5 读数核对 | 已完成（2026-11-24，用户令「先第一章跑，成功后在2-8章逐章跑完」。八章分批＝ch1 `afkp-32fe82f61545` p10–80／ch2 `afkp-371fba5bc444` p81–148／ch3 `afkp-7ee23bb3e322` p149–184／ch4 `afkp-7411ba4a17a3` p185–221／ch5 `afkp-266f50552b55` p222–284／ch6 `afkp-fe1038b4ddb6` p285–347／ch7 `afkp-63126449371f` p348–373／ch8 `afkp-e16f0e49a812` p374–439，共 895 题行/895 题包，ledger 全 `ok`、`failed_chapters:0`、`unmatched_solutions:0`。逐章四判据审核全零（`pack_missing:0`、相对图片 `0`、枚举头 `0`；`unavailable` 仅 ch2/ch4 各 1＝R2 设计内披露）。全书抽查 32/32 题 OK。章节审计脚本＝`tmp/audit_kp_ch.py`，全书终审＝`tmp/audit_kp_book.py`） |

## 对根侧 REQ-179 的回执（S1 判据载体）

| 条目 | 回执 | 排期 |
| --- | --- | --- |
| R1 图片 URL | **落地**（写入面改写 + 8902 内嵌图片读取端点；8900 字节转发零改动；根侧 `MarkdownView` 图片分支按根侧另批） | 本壳 W2，随本轮提交 |
| R2 占位解答 | **落地**（显式不可用标记，占位句不再冒充正文；存量 12 份随 b07 重跑覆盖） | 本壳 W3，随本轮提交 |
| R3 题面串位 | **落地**（先真机取证再收紧题号候选面；存量串位题包随 b07 重跑覆盖） | 本壳 W4，随本轮提交 |
| R4 解答边界 | **落地**（`solution_end` 契约 + 双端切片；须重跑题腿生效，用户已确认） | 本壳 W5，随本轮提交；效果读数随 W7 |

## 验证与验收

- TDD：每条先红后绿；`tests/unit/test_normalize_exercise.py`（切分）、
  `tests/unit/test_engine_kp_gateway.py`（题腿切片与 prompt）、
  `tests/unit/test_knowledge_pipeline.py`（题包组装与标记）、
  `tests/contract/test_api_v1_contract.py` + `tests/contract/test_fixed_document_form.py`（新端点对账）。
- 回归：`pytest tests -q` + `ruff check src tests` 全绿；教材线判据不受影响
  （`split_exercise_items` 既有用例全绿）。
- W7 后回填：b07 重跑读数（截断+越界笔数对比 318）。

### W7 后续：课后练习全量切换（用户 2026-11-24 裁决「全量替换：撤旧+确认新」）

- 确认腿：经 8902 `POST /kp/nodes/{id}/confirm` 批量执行（`tmp/batch_confirm.py`）——旧批次
  `afkp-04add8e31754` 521 行撤回（521/521 ok），新 8 批次 507 行确认（507/507 ok，零 missing）。
  确认期间 REQ-168 W8「确认即同步」回调已把新行逐步投影进 `qe_question`（501 行）。
- 同步腿：`POST /learn/courses/math_analysis/exercises/sync?book=mathanalysis-b07` 回执
  `unchanged:501`、`skipped_no_problem:5`、`skipped_no_solution:1`——6 行 gap 均为源书内容
  边界（5 行指针题「见《数学分析习题集》p.xx」无题面正文、1 行【1212】解答为「未配到解答」），
  同步门按设计跳过，非缺陷。
- 退休腿：旧批次 521 行经 8900 `PATCH /questions/{id}` `retired=true` 软删（521/521 ok，
  `tmp/retire_old_questions.py`）。终态：b07 题库 501 active（全为新批次）+ 521 retired。
- 界面验证（CDP）：`#/practice?domain=math-advanced` 渲染正常；「查看解答」就地展开、翻题、
  换题清解答均工作；展示的 b07 题【5】来源＝新批次 `afkp-32fe82f61545`（`confirmed=1`），
  KaTeX 公式渲染正常。

### S5 读数回填（同口径对比，`tmp/s5_verify_readings.py`）

- 旧基线 `afkp-04add8e31754`：`ok 480 / suspect 289 / reject 108`；verify_note 形态归类
  **截断 148 ＋ 越界（吞邻题）87 ＝ 235 笔**（根侧壳口径 318 含其他形态大类的合计基数）。
- 新 8 批次合计：`ok 682 / suspect 265 / reject 72`；**截断 154 ＋ 越界 31 ＝ 185 笔**。
- 对比：截断＋越界 235 → 185（**降 21%**）；其中越界类 87 → 31（**降 64%**，R4
  `solution_end` 双端切片的直接效果）；suspect+reject 占比 397 行中 79% → 337 行中 50%。
  「显著下降」判据达成（越界类降幅最大、判读基数同口径）。

## 回滚

四条均为算法与产物格式调整：回滚 = revert 对应提交；已落库题行按 `gen_batch` 反查后重跑覆盖。
R1 新端点停用即回退（相对路径改写随旧题包自然失效）。无 DDL、无数据删除。

## 关闭与归档

### S1~S5 关闭判定

| 判据 | 本轮判定 | 证据边界 |
| --- | --- | --- |
| S1 | Achieved | R1~R4 落地回执已在本壳成文；根侧据此自读登记，本仓不写根仓库 |
| S2 | Achieved | 写入面 URL 改写、8902 图片端点、契约守护与 b07 题包相对图片引用为零共同成立 |
| S3 | Achieved | 显式不可用标记落码；b07 重跑后仅设计内两处 `unavailable` 披露，不再把失败固化为占位正文 |
| S4 | Achieved | 真机锚 snippet 定位后收紧候选族；八章重跑 `failed_chapters=0`，枚举头审计为零 |
| S5 | Achieved（本轮替代验收口径） | 根侧未单独重跑 S5；本仓按同口径脚本比较旧／新批次，截断＋越界 235→185、越界 87→31，并经用户授权接受该替代证据 |

**关闭结论（2026-11-25）**：W1~W7 全部有判定，S1~S5 按上表成立。根侧 S5 没有独立执行，
不以本仓替代读数冒充根侧回执；本轮由用户明确授权按替代验收口径关闭 PLAN-037。八章重跑、
507 新行确认、521 旧行撤回与退休、根侧 501 active 投影、练习界面 CDP 验证均保留为本壳证据。
归档判定改判 **Retain**：上述不可替代证据尚未形成有效 Git 锚点，不能按原判 `Delete` 删除。
收口按 [任务生命周期](../../../standards/task-lifecycle.md)「计划生命周期」六步顺序执行。
