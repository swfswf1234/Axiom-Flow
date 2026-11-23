# 教材内容解析轮：种类审校、点梳理与章文件夹（content-curation-round）

状态：Completed
任务类型：B
最后更新：2026-11-09
关联 ADR：[ADR 0004](../../../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期与管线归属）、
[ADR 0015](../../../adr/0015-db-single-source-for-readiness.md)（就绪单源）、
[ADR 0016](../../../adr/0016-af-family-rebuild-into-four-tables.md)（`af_kp` 形状与扩展槽）、
[ADR 0019](../../../adr/0019-curation-outcome-and-book-kind-columns.md)（书种类与梳理结局位）
关联设计：[knowledge-analysis.md](../../../design/knowledge-analysis.md)（`KA-27`／`KA-29`／`KA-36`／`KA-43`／`KA-44`／`KA-45`／`KA-46`／`KA-51`／`KA-57`／`KA-58`／`KA-60`／`KA-61`／`KA-63`／`KA-64`／`KA-65`／`KA-66`）、
[parsing-pipeline.md](../../../design/parsing-pipeline.md)（`PP-7`~`PP-10` 版面块与 `index`）、
[api.md](../../../architecture/api.md)（`KpNodeOut`、批次流水与工作区）、
[database-design.md](../../../architecture/database-design.md)（`af_kp` 列语义与 `meta` 扩展槽）
关联 Tracker：docs/trackers/todo.md（PLAN-032 镜像行，挂在 ARCH-035 聚合行下）
归档判定：Retain（例外归档：教材三书 `stage=content` 真机窗口经用户 2026-11-09 裁决跳过；豁免裁决与 W1~W8 本地逐条读数不可再生，原判 `Delete` 改判）

## 目标与成功标准

**目标**：把教材的 `stage='content'` 从「逐节整页拼 markdown、一次问一句总结」改成一条可复核的内容链——
**先定类、再按块取材、按预算切分或合并、点齐之后再写章、详情带取材区间**。四步全在 `content` 一个阶段内，
`stage` 四值闭集、端点形状与 `af_kp` 列语义不动。成功后一套内容批次读得出「哪一段是习题、模型读了哪几块、
哪些节被切窗或合批、章前材料止于哪里」。

1. **S1 种类审校**：知识点段内的大块习题被拆成并列习题行（父＝所属章、`model_ref='rule:exercise_split'`、
   `meta.toc_kind='exercise'`＋`toc_role='reference'`、零模型调用）；判不出类的行进点名面而不改写；既有目录行
   `name`／`anchors`／`gen_status`／`rev` 逐列不变；重跑只作废旧拆分行。
2. **S2 点梳理取材**：模型输入只由七类非家具正文块组成（段落／标题／公式／表格／图片／列表／题注，家具与目录页块零入），单次材料正文 ≤ 10,000 字符；
   超预算节点按块边界切窗（不切断块），相邻小节点合批且每点仍有独立总结与详情；窗口或合批缺项按失败节披露。
3. **S3 章文件夹**：章腿在本批子点总结齐后发起，材料只含章锚页到首点子行锚页之前的块与子点总结；
   无子点的章零调用；不覆盖 `confirmed=1` 与非空 `summary`。
4. **S4 详情**：点行 `meta.material_ranges` 记本次取材的块区间，工作区／单行回显／导出三面同值；
   公式保留 LaTeX 定界符、表块保留 HTML 原文、图块只给路径与题注（零重编码、零缩放）。
5. **S5 流水读数**：`result` 闭集由 36 扩为 42，新增六键各自有值有写者，零值出 `0`／空数组。
6. **S6 回归**：`full`／`outline`／`exercise` 三值的取材、调用数与写入面逐字不变；教材三书真机窗口经用户 2026-11-09 裁决跳过，本壳按例外收口。

## 范围与非目标

**范围**＝`content` 阶段的取材、预算、切窗／合批、种类审校、章文件夹次序、详情取材区间、六枚流水键，
以及 `knowledge-analysis.md`／`api.md` 的同步；**零 DDL、零新端点、零新阶段、零新列**。

**非目标**＝① 不改 `stage` 四值与受理门组合闭集（`KA-51`）；② 不改 `af_kp` 列语义与 `meta` 之外的任何槽；
③ 不改目录腿（判页、整理、核对、定锚、成行）与显示段派生（`KA-59`~`KA-62`）；④ 不做提示词质量实验
（C 类另批：样本、内容哈希、预算与采纳门槛先冻结）；⑤ 不实现 8903 界面样式（等比、不放大、`max-width: 100%`、
表格横滚由消费方按格式契约实施）；⑥ 不在本壳内跑真实模型窗口（归用户发令的 D 类窗口）。

## 前置条件

1. PLAN-031 的三处真机修正已验收：b18 习题行只保留章尾一枚、b12 目录页残留行零枚、三级细目折入二级；
   b12 批次 `afkp-f8e141674dc7` 的收尾读数（`folded_sub_items>0`、未定位点名）到齐后才开始执行本壳。
2. 用户 2026-11-08 已裁口径（见下节），未再裁的项按「维持现状」处理、不按建议默认采纳。
3. 模型腿均有确定性替身（`tests/unit/test_knowledge_pipeline.py` 与 `tests/unit/test_engine_kp_gateway.py`
   的既有替身），本壳的本地判据不引入新出站依赖；真实窗口另归 W8 的用户发令。
4. 本地解释器 `D:/software/anaconda3/envs/QED_env/python.exe`，门禁脚本走 `tmp/run_pytest.py`（平台探针绕行）。

## 已裁口径（2026-11-08 用户确认）

| 项 | 裁决 |
| --- | --- |
| 输入预算 | 按 **10,000 汉字**（字符近似、不引分词器），为思维链留余量；提示词与回复形状另计 |
| 切分／合并 | 超预算按块边界切窗（不切断块，单个超大块才按句界）；相邻小节点合批，每点仍有独立输出 |
| 习题行归属 | 知识点段内的大块习题拆成该章下的并列习题行；不用模型判类 |
| 章文件夹边界 | 首个子点锚页之前的内容归章（同页只取章标题页），点从自己的标题块起重读 |
| 详情存储 | `meta.material_ranges` 记取材块区间，零新列；读面同名派生键三面同值 |
| 正文格式 | 公式保留 LaTeX 定界符；表格 HTML 原文＋屏侧横滚；图片等比、不放大、`max-width: 100%` |
| P0 位置 | 放在 `content` 批次内、点腿之前；不新增阶段，`stage` 闭集不动 |

## 现状链路（代码事实）

| 步 | 现在做什么 | 落点（代码事实） | 与目标口径的缺口 |
| --- | --- | --- | --- |
| 取材 | 逐页整页 markdown（剥家具行后）拼成一节材料，不按块选、不设上限 | `orchestrator/knowledge.py:_page_bodies`、`_section_spans` | 书眉页脚之外还带目录回声与无关节内噪声；无预算，超长节一笔打满 |
| 点腿 | 每节一笔，回复逐点给 `summary`＋`must_master`＋`key_definitions`；无切窗、无合批 | `extract_points`（content 分支）、`engines/kp_gateway.py:_POINTS_PROMPT` | 长节上下文压力大、短节固定开销占比高；失败节无「哪一窗」的概念 |
| 种类 | 写时不审校；`node_type` 只读时派生 | `normalize/toc.py:node_type_of`、`KA-46` 的章尾规则（仅目录腿） | 知识点段内混着的习题段没有行；b18 形在目录腿下整章零行，`content` 下无人处理 |
| 章腿 | 两趟：先取章首材料，再读子点 `summary`；子点未齐时章腿照发 | `extract_chapters` | 章材料含章标题页与章前正文，边界未钉在首点之前；次序无「子点齐后再写」的硬约束 |
| 详情 | `must_master`／`key_definitions` 落 `meta`，读面透传 | `_extract_content_points`、`KpNodeOut`、`KP_META_KEYS` | 详情结论无法回指「读了哪几块」 |
| 流水 | `result` 闭集 36 键 | `db/repositories.py:KP_RESULT_KEYS` | 审校、切窗、合批、取材区间四类读数无位 |

## 目标流程（P0 → P3）

1. **P0 种类审校**（点腿之前，规则腿、零模型调用）：区间内逐枚在册目录行判类——整块习题短标签行只点名、
   不发点腿；区间内出现整块习题短标签的知识点行，按标签块锚点拆出并列习题行。三枚读数入流水。
2. **P1 点梳理**（模型腿）：每节点按块取材 → 预算内单窗、超预算切窗、小节点合批 → 逐点收
   `summary`＋`must_master`＋`key_definitions`＋取材区间；任一窗口失败整节零行并入失败清单。
3. **P2 章文件夹**（模型腿，本批内串行在 P1 之后）：章前材料（章锚页到首点子行锚页之前）＋子点总结 →
   章 `summary`＋导读；无子点章零调用。
4. **P3 详情**：点行 `meta.material_ranges` 与三面读键随行落库；正文原文透传、尺寸归屏侧。

## 工作项

| 项 | 内容 | 状态 |
| --- | --- | --- |
| W1 | 条文落档：`KA-63`（P0 种类审校）、`KA-64`（块取材与 10,000 字符预算）、`KA-65`（章文件夹边界与次序）、`KA-66`（详情取材区间与格式契约）；`KA-27` 键数 36→42、`KA-36` 作废面、`KA-43` 新增行与取材、`KA-46` 仅目录腿；失败表五行与验收 31~34；`api.md` 六键、`material_ranges` 与 P0 段 | 完成（2026-11-08：条文与端点口径已落，未动代码） |
| W2 | 纯函数与单测先行：`normalize/toc.py:kind_review`（拆分行读数＋点名清单）、`select_material_blocks`（块选择）、`plan_point_calls`（预算切窗与合批计划）、`chapter_material_blocks`（章前材料按块取）；零 IO、零模型 | 完成（2026-11-08） |
| W3 | P0 接入 `extract_points`：拆出行写入（两枚身份位、父＝章、`rule:exercise_split`）、标签行跳过，三枚流水键（`type_review_rows`／`type_review_flags`／`split_exercise_rows`）全链 | 完成（2026-11-08） |
| W4 | 块取材与预算：`select_material_blocks` 七类块选择、窗口与合批发起、材料带「p{页}·b{序}」前缀、失败节语义与 `only_failed` 对齐；两枚流水键（`point_windows`／`point_merges`） | 完成（2026-11-08） |
| W5 | 章文件夹：`_chapter_material` 改块选择、`extract_chapters` 在点腿完成后发起、`failed_chapters` 读已落库点总结；无子点章零调用 | 完成（2026-11-08） |
| W6 | 详情：`_extract_content_points` 经 `KpRepository.set_material_ranges` 写 `meta.material_ranges`（只为本批写成总结的行）、`KpNodeOut` 派生键与三面同值、`KP_META_KEYS` 登记、`material_ranges_rows` 流水键 | 完成（2026-11-08） |
| W7 | 契约与回归：`test_api_v1_contract`（六键、`material_ranges` 三面、`content` 提交读数）、`test_db` 闭集 42 键、`full`／`outline`／`exercise` 三值不变式；`api.md`／`knowledge-analysis.md` 与实现逐句对账 | 完成（2026-11-08：契约用例与 42 键钉到齐，`api.md`／`knowledge-analysis.md` 已按实现改齐；顺带修 `KP_LEDGER_STEPS` 漏登 `chapter`／`exercise` 的潜在崩溃） |
| W8 | 门禁与真机窗口：本地 `pytest tests -q`＋`ruff check src tests` 全绿；教材三书 `stage=content` 真机窗口按用户发令执行，读数按 S1~S5 逐条核对 | 完成（2026-11-09：用户裁决 W8 的教材三书 `stage=content` 真机窗口跳过并收尾；本地门禁全绿——`1569 passed／18 skipped`＋`ruff` 全绿；本壳按例外归档） |

## 验证与验收

- **测试层**：纯函数单测覆盖预算边界（恰好 10,000／超一字符／单块超预算／块边界不切断）、合批下限与上限、
  拆分行身份位与去重、章材料止于首点之前；管线单测覆盖 P0 与点腿调用数不变、窗口失败整节零行、
  章腿次序与不覆盖；契约测试覆盖六枚流水键恒发与 `material_ranges` 三面同值；DB 层守 `KP_RESULT_KEYS` 42 键。
- **回归层**：`full`／`outline`／`exercise` 三值下取材、调用数与写入面逐字不变（既有守护用例不改判）；
  `content` 的在册目录行既有列逐列不变。
- **真机层（W8）**：原计划由用户发令跑教材三书 `stage=content` 窗口（b18 拆出并列习题行且点腿调用数不变、
  b12 切窗读数与章段边界可读、b05 逐行结构不变、`material_ranges` 与调用块集合可比对）；用户 2026-11-09
  裁决跳过该窗口、本壳按例外收口，故真机读数不构成本壳的关闭判据。
- **文档层**：`tests/contract/test_design_documents.py`、`test_architecture_documents.py` 与
  `test_code_document_mapping.py` 通过；`docs/trackers/todo.md` 镜像行与正文状态逐字一致。

## 回滚

代码侧按工作项粒度提交、逐项可回退：P0 与预算各自有独立开关点（同一函数入口），回退时删调用点与新增键，
不动既有腿。`meta.material_ranges` 是附加键，读面缺键归空表，回退无需清库（存量键保留但不外发）。
真机窗口若读数不符，按 `KA-36` 重跑同范围新批次覆盖，不改已确认行、不删库。

## 收口结论（2026-11-09）

W1~W8 完成，S1~S5 判据由单测、契约与 DB 层守住：P0 种类审校、七类正文块的取材与 10,000 字符预算
（切窗／合批）、章文件夹边界与次序、`meta.material_ranges` 三面同值、`result` 42 键闭集、三值阶段
回归不变；本地门禁全绿（`pytest tests -q` 1569 passed／18 skipped、`ruff check src tests` 通过）。

**例外收口**：W8 的教材三书 `stage=content` 真机窗口经用户 2026-11-09 裁决跳过，S6 的真机读数不成立；
用户明确要求跳过并收尾。真机内容窗口若后续重启，应另立窗口任务，不在本壳补记。因该豁免裁决与
W1~W8 的本地逐条读数不可再生，本壳由 `Delete` 改判 `Retain`，移入 `docs/history/plans/2026-11/`。

## 关闭与归档

各工作项交付且本地门禁全绿后：承载正文写结论 → todo 镜像行压缩并关闭 → `completed.md` 迁移 →
`plans/index.md` 登记 **Retain**（例外归档：真机窗口经用户裁决跳过，豁免裁决与本地逐条读数不可再生）→
`project-status.md` 刷新。真机窗口未跑完时不得把本壳收口为 Completed，最多停在 In Progress 并写明待办；
本壳的 Completed 以用户 2026-11-09 的显式跳过裁决为前提。
