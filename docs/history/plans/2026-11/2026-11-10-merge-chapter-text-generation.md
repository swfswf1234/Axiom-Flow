# 课程级章总结与导读生成腿（merge-chapter-text-generation）

状态：Completed
任务类型：B
最后更新：2026-11-26
关联 ADR：[ADR 0004](../../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、管线归本仓）、
[ADR 0015](../../adr/0015-db-single-source-for-readiness.md)（就绪单源）、
[ADR 0020](../../adr/0020-shared-table-write-and-llm-accounting-boundary.md)（共享表业务写入与调用记账边界）
关联设计：[knowledge-analysis.md](../../design/knowledge-analysis.md)（`KA-16` 共享表边界、`KA-23` 调用账、
`KA-43` 点腿与节级总结、`KA-45` 章腿两段、`KA-48` 单点重生成、`KA-57` 详情提示词冻结）、
[api.md](../../architecture/api.md)（`kp` 路由与响应形状）、
[model-integration.md](../../design/model-integration.md)（8900 文字网关与槽位）
关联 Tracker：docs/trackers/todo.md（PLAN-034 镜像行已随收口移除；根侧 REQ-163 原函已归档，本壳保留 Axiom-Flow 侧能力）
归档判定：Retain（保留 W0 不变式推导、独立评审三处缺陷的修复与门禁证据；2026-11-26 用户免除 W1 真模型 demo）

## 目标与成功标准

**目标**：课程级章总结与两段学习导读由 Axiom-Flow 端到端负责：AF 接收多本证据，复用现有章腿生成、
检索、校验与重试，经 8900 文字网关完成统一调用记账，并直接更新共享 `qed_knowledge_point` 中明确的课程级
章行。8900 负责共享表结构与调用账治理，不是共享表或知识点主表的独占写者；根侧不另建课程级生成逻辑。
用户确认该能力可用，但排在 C7 与 PLAN-035 之后实施。

1. **S1 生成与落库形状**：请求显式带 `course_id` 与 `chapter_kp_id`，证据包为 `chapter`＋`sources[]`；
   返回 `{summary, narrative, check, persistence}`。AF 直接更新目标章行，不能只返回文本由根侧另写一套生成链。
2. **S2 口径复用**：冻结的两段导读提示词、章级检索（中文维基优先→英文维基→数学专业源，≤8 条、限长去重、
   fail-open）、确定性校验与同模型一次重试全部复用 PLAN-033 的 `C-1`／`C-2`／`C-4`／`C-5`／`C-6`。
3. **S3 失败语义**：证据缺块或目标身份不匹配 422 并点名；目标行不存在 404；人工确认态保护或生成后状态改变
   409；文字槽位不可用 503；两次校验不过 → 200 照写并 `check.passed=false` 带 `issues` 与模型名；落库失败不得
   伪装为生成成功。
4. **S4 归因**：每次生产调用只由 8900 在 `qed_llm_calls` 写一行，`step='chapter'` 可指认本腿；AF 不重复
   直写同一笔，不预设具体模型、不切换共享槽位。
5. **S5 回归**：既有章腿／点腿、批次账本、`stage∈{full,outline,content,exercise}` 四值与端点面逐字不变；
   默认门禁零出站。
6. **S6 责任边界**：共享 `qed_*` 表允许跨项目读写；8900 维护结构与统一记账，但不独占 `qed_knowledge_point`。
   既有 `af_kp` 提升仍保持 8900 拉取式单向；本腿是 AF 直接写共享主表的独立课程级通路，不产生第二套生成链。

成功标准条目化判据的正本在 `docs/trackers/todo.md` 的 PLAN-034 行，本壳只承接拆解与证据。

## 范围与非目标

**范围**：生成腿路由与请求／响应模型、无页块溯源的课程证据模型、证据到章腿材料的映射、检索与校验的
复用接线、8900 统一调用账归因、AF 直接写共享表的事务与文件通路、单测与契约、`api.md` 与 `KA-16`／
`KA-23`／`KA-45` 条文随步。

**非目标**：不改 PLAN-033 的提示词本体与检索源；不做点级检索；不新建表、不改既有表列；不做多语言模板；
不把根仓库代码改动纳入本仓；不重复直写 `qed_llm_calls`；不在根侧实现第二套课程级生成逻辑；不把既有
`af_kp` 拉取提升误读为“AF 永不直写共享主表”。

## 前置条件

1. PLAN-033 的 W2~W6 已落地（两段提示词、章级检索包、模型名只读探测、校验与重试链）。
2. 8900 文字网关可达且当前生效槽位可用；出站唯一通道不变。
3. 根侧 REQ-163 原函已归档为 Not Applicable；本计划作为 AF 侧能力保留，不表示根侧链路仍被阻塞。
4. 共享表落库责任已由用户裁归 AF；共享表允许跨项目直接读写，W0 不再讨论“是否有写权”，只冻结目标身份、
   事务、幂等、确认保护与回滚不变式。
5. 零 DDL；端点与落库通路不新建表或新增列。
6. 新增文档须同步本仓文档白名单与 `pytest tests/contract`。
7. 根侧总纲仍保留共享表独占写的旧口径，需由根侧按 ADR 0020 同步；根仓库本仓只读，不代改。该文档同步不阻塞本仓按用户已确认的写权边界准备实现。

## 接口契约（拟）

```
POST /api/v1/kp/merge/chapter-text
request {
  "course_id": str,
  "chapter_kp_id": str,
  "chapter": {"name": str, "summary": str?, "narrative": str?},
  "sources": [{"book_id": str, "title": str?, "chapter_name": str,
               "summary": str?,
               "points": [{"name": str, "summary": str?}]}]
}
response {
  "summary": str,                 # 课程级一段话（建议 ≤1000 字）
  "narrative": str,               # 冻结提示词的两段导读：总问题／难点 → 材料如何推进到结论
  "check": {"passed": bool, "issues": [str], "model": str},
  "persistence": {"kp_id": str, "evidence_sha256": str,
                  "narrative_ref": str, "reused": bool}
}
错误：404 目标行不存在；422 证据缺块或 course_id／plane／node_kind 不匹配；409 confirmed／rejected
保护或生成后目标状态改变；503 文字槽位不可用；其余按既有错误面。
```

`chapter_kp_id` 是显式目标身份，不按名字猜行、不新建行。服务端核验目标存在且 `course_id` 匹配、
`plane='course'`、`node_kind='outline'`，并拒绝覆盖 `confirm_state='confirmed'` 或 `'rejected'` 的行。

`chapter` 与 `sources[]` 映射为章腿材料的章前材料／子点总结／检索资料；检索只由本章名与
`sources[].points[].name` 派生。课程证据没有页块溯源，新增无页码输入的 `ChapterTextInput`，既有
`ChapterInput` 不动，不伪造 `page=0`、锚点或 `AfKp` 行，也不直接复用依赖它们的 `_chapter_material()`。

### W0 冻结的落库不变式

1. **证据哈希**：由目标 `kp_id`、规范化证据包与提示词版本确定性计算 `evidence_sha256`；同目标同哈希已有
   完成结果直接回读，不再次调用模型。
2. **目标锁与确认保护**：生成前只读预检；模型调用在锁外。生成后重新锁定目标行，复核身份、确认态与证据
   哈希未变，再提交；任一变化即 409，不覆盖。
3. **正文与引用同事务**：`summary`、`narrative_ref`、`meta.chapter_text` 在同一目标行锁、同一事务内提交，
   不留“正文已写但引用未落”或反向半写。
4. **导读文件原子写**：使用不可变内容寻址路径
   `parsed/course/<course_id>/<kp_id>/<evidence_sha256>.md`；`narrative_ref` 存数据根相对路径。新路径按 UTF-8
   原始字节经同目录临时文件原子落盘，不做换行翻译；已有路径逐字节相同才接受，不同即 409，绝不覆盖。
   DB 失败最多留下不可见孤儿文件，不覆盖旧正文。
5. **失败可恢复**：失败不改变旧 `summary`／`narrative_ref`／`meta.chapter_text`；回滚键为目标 `kp_id` 与旧
   证据哈希，不物理删除他人数据。
6. **8900 统一记账**：本腿引擎声明 `accounts_externally=True`，AF 不启用本地账本包装器。用确定性
   `task='kp-merge-chapter-<hash前缀>'`、`step='chapter'` 与
   `prompt_template='kp-merge-chapter/<完整evidence_sha256>@<提示词版本>'` 归因；不确定是否已出站时，先查
   `/api/v1/llm/calls`，按 service／task／step／status／prompt_template 筛选，并逐字核对完整
   `prompt_template` 与原 `prompt` 后才复用 success 的 `response`。根网关当前无幂等键，查询恢复不等于
   exactly-once，匹配不唯一或原请求无法核对时不恢复、不擅自重发，W0 必须把这一限制写成可接受边界。

### W4 冻结的持久化形状

1. `meta.chapter_text` 只存证据与校验读数：`evidence_sha256`、`prompt_version`、`summary_sha256`、
   `narrative_sha256`、`generated_at` 与 `check`；正文仍只落内容寻址文件。
2. 同目标同哈希仅在目标身份／确认态仍合法、`narrative_ref` 等于预期路径、文件存在且两类内容哈希均吻合时
   回读，`reused=true` 且不再调用模型；完成记录损坏或文件缺失／被改时判 409，不猜测、不自动重发。
3. 预检后结束读事务再生成；生成后在新事务锁行并复核身份、确认态与旧证据哈希。写文件发生在目标锁内、
   三列更新之前；DB 回滚最多留下不可见孤儿文件，旧行值不变。
4. 出站结果不确定时只按 service／task／step／完整 `prompt_template` 与原始 `prompt` 逐字核对唯一 success
   账本行恢复；零条、多条、账本不可达或 prompt 无法核对均不自动重发，按 503 披露。
5. 课程级重试调用失败直接按 503 结束，不把不确定出站结果改写成首版成功；书籍章腿既有重试兜底不变。
   导读文件按 UTF-8 原始字节写读与哈希；已有内容寻址文件逐字节相同才接受，不同即 409 且不更新共享行。

## 工作项

| 条目 | 内容 | 状态 |
| --- | --- | --- |
| W0 | 责任与落库设计：按上节冻结显式目标身份、AF 直接共享表写入路径、证据哈希与幂等、confirmed／rejected 保护、文件与事务原子性、回滚和 8900 调用账恢复 | 已冻结（2026-11-26；仅设计，不含生产实现） |
| W2 | 证据包模型与映射：请求／响应 Pydantic 模型、无页块溯源 `ChapterTextInput`、证据到章腿材料的组装、空证据与缺块 422 | 完成（2026-11-26）：严格请求／响应模型、序列规范化与证据哈希、无页码映射；`tests/unit/test_chapter_text.py` 8 passed，零出站 |
| W3 | 生成接线：复用 PLAN-033 的两段导读提示词、检索包、校验与同模型重试；`check` 组装；8900 统一账本归因 | 完成（2026-11-26）：课程级无页块提示词、多书材料、fail-open 检索、一次同模型重试与 `check`；哈希绑定 `task`／`step`／完整 `prompt_template`，无 `model` 字段；课程文本 15 passed、网关 133 passed，零出站 |
| W4 | 失败面与持久化实现：503 槽位不可用透传、超时上限、目标锁／确认保护、同事务三列更新、内容寻址文件原子写、幂等回读与「不半写」；单测覆盖 404／409／422／503／照写 | 完成（2026-11-26）：生成、目标保护、共享表三列同事务、内容寻址文件、幂等回读、API 与查账恢复已交付；独立复核修复课程级重试吞错、Windows 字节一致性与历史文件覆盖，定向 188 passed，Critical／Important 均为 0 |
| W1 | **demo 门**＝在 W2~W4 能力齐备后，用现成真证据（b05 第 3 章材料＋b18 同章材料）调生成腿一次，回读 `summary`／`narrative`／`check`／`persistence`，并由 AF 落库通路回读；到门暂停待人工验收 | 不适用（2026-11-26 用户裁决免除；不再执行、不再阻塞本计划关闭） |
| W5 | 条文与门禁：`api.md` 路由与响应形状、`KA-16`／`KA-23`／`KA-45` 随步、文档白名单；`pytest tests -q`＋`ruff check` | 完成（2026-11-26）：`api.md`／`KA-16`／`KA-23`／`KA-45`／`KA-74`、code-map 与文档索引已同步；`QED_TEST_MYSQL=0` 下 `pytest tests -q`＝1823 passed／18 skipped，`ruff check src tests`＝All checks passed |

执行顺序＝W0 → W2 → W3 → W4（含持久化实现）→ W5；W1 原位于 W4 与 W5 之间，已由用户免除，不补跑。

## 过程与证据

- 2026-11-26｜W2／W3／W4 代码、严格 API 契约、共享表持久化与不确定出站查账恢复已交付；默认测试只用
  临时 SQLite、临时数据根与假引擎，未启用 `QED_TEST_MYSQL=1`，未连接模型或运行库。
- 2026-11-26｜独立复核发现并修复三处缺陷：课程级重试失败不再回退首版、多行导读按 UTF-8 原始字节写读与哈希、
  历史内容寻址文件不同内容返回 409。新增五条回归先红后绿；相关四个测试文件 `188 passed`，独立复核
  Critical 0／Important 0。W1 真模型 demo 仍未执行。
- 2026-11-27｜残局修复证据（只追加，不改写上文历史关闭判据）：独立审查发现课程级章腿在内容寻址文件写成功后，`update_text` 或最终 commit 失败会留下孤儿文件，导致同证据重跑生成不同正文时永久 409。现由编排层 `_persist` 在同一事务边界提交三列；UPDATE／提交异常即 rollback，并只回收本次新建路径，调用前已存在的同内容文件不删。新增 update 失败、commit 失败、既有文件保留与失败后换正文重跑回归：红灯 `3 failed`，修复后该组 `5 passed`；课程章定向回归 `tests/unit/test_shared_chapter_text.py`＋`tests/unit/test_chapter_text.py`＋`tests/contract/test_chapter_text_api_contract.py` 共 `50 passed`。`api.md`／`KA-74` 已同步当前失败回收语义；默认回归未启用 MySQL、未访问真实数据根或模型。
- 2026-11-27｜歧义提交修复证据（只追加）：二次独立审查指出 commit 可能已在服务端成功、客户端却收到断连异常，原统一回收会删掉数据库已引用的正文。新增“先提交再抛断连异常”回归，红灯为已提交行引用存在但文件缺失；现 commit 异常先 rollback 再回读目标行，仅确认未引用本次 `narrative_ref` 时回收，回读失败则保留文件。UPDATE 失败回收与确定提交失败回收回归保持通过。定向回归 `72 passed`，文档契约 `396 passed`，全量 `1848 passed, 18 skipped`，Ruff 全绿，MySQL 集成 `17 passed`；`api.md`／`KA-74` 已同步。未访问真实 dataset、运行库或真实模型。
- 2026-11-26｜收口门禁：`QED_TEST_MYSQL=0` 下 `pytest tests -q`＝1823 passed／18 skipped；
  `ruff check src tests`＝All checks passed。显式 MySQL 层、真实模型与运行库均未触达。

## 验证与验收

- 单测：形状与映射、空证据、缺块 422、目标不存在 404、身份不匹配 422、confirmed／rejected 409、槽位不可用
  503、两次校验不过照写（`check.passed=false`）、检索 fail-open；默认门禁以替身实现，不出站。
- 幂等：同目标同证据哈希二次请求不再出站，返回 `reused=true`；证据变化生成新哈希，不猜名字命中别的行。
- 落库：AF 通路直接更新共享主表并回读；`summary`／`narrative_ref`／`meta.chapter_text` 同事务；失败不半写；
  生成后确认态改变即 409；根侧无第二套课程级生成逻辑。
- 文件：同哈希内容寻址、同目录暂存、新路径按 UTF-8 原始字节原子写入；已有文件相同接受、不同 409，
  故障不覆盖旧正文，孤儿文件不进入 `narrative_ref`。
- 账本：生产调用由 8900 写一行，AF 本地写入计数为 0；不确定重试先查账恢复，不重复出站。
- 契约：`openapi.json` 出现新路由且与 `api.md` 一致；既有 `kp` 路由逐条不变。
- 联调：W1 的 b18＋b05 真模型 demo 与落库回读由用户于 2026-11-26 明确免除，不把本地替身回归
  冒充真机联调，也不将该未执行面计入自动门禁通过范围。
- 回归：`full`／`outline`／`content`／`exercise` 四值与点腿调用数逐字不变；既有批次账本零变化。

## 回滚

- 撤回生成路由、落库接线与实现；无迁移、无新表、无新列。
- 单次写入按 `kp_id`＋旧证据哈希恢复旧 `summary`／`narrative_ref`／`meta.chapter_text`；内容寻址文件不被
  物理删除，孤儿文件按只读清单另行清理。
- 调用账是追加式审计事实，不删除、不重建；已有章腿、批次与既有提升通路不受影响。

## ARCH-042 聚合壳关闭（2026-11-26）

用户裁决：模型真机窗口反复超时，不再把 `ARCH-042` 聚合壳作为课程级能力的完成门；该行按**退役聚合壳**
关闭，关闭证据迁入 [completed.md](../../trackers/completed.md)。`ARCH-042` 没有独立计划文件，故不产生
Retain／Delete 归档动作；其残余承载由本计划收口。

**关闭不等于 PLAN-034 完成**：W2／W3／W4 已完成，覆盖生成、目标保护、内容寻址文件、共享表三列同事务、
幂等回读、API 契约与不确定出站查账恢复；独立复核的三处缺陷已修复，Critical／Important 均为 0。W1 的
b05＋b18 真模型 demo 未执行；W5 的 `api.md`／`KA-16`／`KA-23`／`KA-45`／`KA-74`、code-map 已同步，
收口门禁为 1823 passed／18 skipped 与 Ruff 全绿。2026-11-26 用户进一步明确知识梳理轮三块功能与全流程验收
结束，并免除 PLAN-034 仅剩的 W1 真模型 demo；本计划据此关闭，未执行面不再作为阻塞项。
PLAN-035 保持 `In Progress`，只允许不落表 dry-run，其重跑与条文欠账不随本计划关闭。

## 关闭与归档

2026-11-26 收口：W0、W2～W5 已完成，W1 经用户裁决免除；PLAN-034 镜像行移除，关闭证据写入
`completed.md`，计划按 **Retain** 迁入 `docs/history/plans/2026-11/`。`api.md`、
`KA-16`／`KA-23`／`KA-45`／`KA-74` 与 code-map 已同步；全量自动门禁通过。未执行真实模型 demo、
显式 MySQL 层与运行库写入，不以 Retain 归档替代这些外部验证。
