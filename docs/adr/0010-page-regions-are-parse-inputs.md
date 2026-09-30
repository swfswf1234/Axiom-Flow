# ADR 0010：页框图是页级解析输入，不是版本内覆盖层

状态：Accepted
最后更新：2026-10-01
关联代码：`src/axiom_flow/db/models.py`（`AfPageRegion` 与 `AfBlockEdit` 唯一键）、
`src/axiom_flow/db/repositories.py`（`RegionRepository`/`RegionIntent`/`region_id_for`、
`EditRepository`）、`src/axiom_flow/migrations/versions/20260926_0002_page_regions.py`、
`src/axiom_flow/api/main.py`（`put_structure` 的三选一定位与几何锚换算、`edit_block` 的下标校验、
`get_page` 的读取作用域）、`src/axiom_flow/engines/base.py`
（`ParseEngine.parse_page`）、`src/axiom_flow/engines/mineru.py`（`_submit`/`_run` 请求字段）、
`src/axiom_flow/orchestrator/pipeline.py`（页级解析路径）
关联测试：`tests/unit/test_db_page_regions.py`（表不变式与仓储）、
`tests/contract/test_api_v1_contract.py`（`TestStructureEndpoint` 写入面；区域读视图投影待实施）、
`tests/unit/test_orchestrator.py`（每框一次调用与退化路径，待实施）、
`tests/contract/test_storage_conventions_governance.py` 的落盘面复用

## 背景

根仓库 REQ-099（承接壳 [PLAN-018](../plans/2026-09-26-parsing-region-edit-typography-request.md)）
要求本仓库具备三项能力：R1 区域驱动页解析（框图作为该页解析输入，画 3 框→解析→恰 3 块且文字为
框内内容）、R2 块结构编辑持久化（删除/合并/新增区域，重启仍在、重解析后删除块不复活）、R3 页框图
读取视图（返回当前生效区域集合，作 8903 画布唯一数据源）。把这三条对照到现状，现有机制表达不了：

1. **引擎协议没有区域入口**：`engines/base.py` 只有 `parse_document(pdf, filename, start_page,
   end_page)` 与 `parse_page(image_bytes, page_no)`；`engines/mineru.py` 的提交表单只带产物形态
   开关 `_OUTPUT_FLAGS` 与窗口 `start_page_id`/`end_page_id`，无任何 bbox/区域字段。故「把框交给
   引擎识别」在本仓不可用，区域驱动只能是管线侧方案（框内裁剪后按区域提交）。
2. **块编辑覆盖层的定位键承载不了人工意图**：`af_block_edits` 唯一键是
   `(book_id, job_id, page_no, block_index)`，且 `api/main.py` 的 PUT 先校验 `block_index` 落在
   **生效版本**产物块数组内。这套键的前提是"被编辑的对象已经存在且有版本内下标"——用户新画的框
   在解析之前没有下标，天然无处可记。
3. **绑版本会让判据不可达**：`block_index` 只在解析版本内有意义（[PP-7/PP-9](../design/parsing-pipeline.md)：
   映射表或归一化规则一变，下标语义即漂移，故编辑必须绑定它核对时所在的版本）。但 R1 的验收判据
   是「对已解析页改框后**再解析**，块随框变化」，R2 的判据是「删除块在后续重解析中不复活」——
   人工框图与人工结构意图恰恰**必须跨版本存活**。把新东西塞进 `job_id` 作用域会自相矛盾：新解析
   产生新 job，用户刚画的框在新 job 上读不到。
4. **落库形态受存储规范约束**：根仓库 ADR 0018 禁止把元数据写成产物侧 JSON 岛；本仓的对应口径是
   `af_*` 表 + Alembic 迁移（[ADR 0005](0005-mysql-integration-test-isolation.md) 的隔离边界、
   [ADR 0009](0009-data-root-must-be-explicit.md) 决定 4 的「写入面锁在 `parsed/`」）。区域若写成
   `versions/<job_id>/regions.json` 就是第二个产物正本，与「版本目录即一次解析的自证快照」冲突。

## 决定

1. **人工意图落在页级作用域，不绑解析版本**：新增 `af_page_regions`，唯一键
   `(book_id, page_no, region_id)`，**不含 `job_id`**。`region_id` 为服务端生成的稳定不透明 id；
   几何为原页图像素 `[x0, y0, x1, y1]`、左上原点、基准 `pages/p<NNNN>.png`（与 PP-7 的 `bbox`
   同一坐标系，不引入第二套坐标约定）。该表承载的是**输入与意图**（待识别框、删除意图、合并
   意图），不是解析结果。
2. **`af_block_edits` 的语义保持不变**：文本修正与几何修正（`corrected_text`/`corrected_bbox`）
   仍绑 `(book_id, job_id, page_no, block_index)`，即仍是"对某一次解析结果的核对结论"。两表的
   分工固定为：**页级=人的意图（跨版本），版本级=人对结果的判定（不跨版本）**。任何把页级意图
   写进版本级表、或反向的做法判为缺陷。
3. **产物文件仍是唯一正本，区域视图由服务端投影合并**：引擎块仍只落在 `versions/<job_id>/pages/`
   的产物里，库内不写第二份块数据。R3 的「当前生效区域集合」= 人工区域（页级）+ 引擎块（生效
   版本产物）在**服务端**投影成的统一视图，逐区域标出来源与状态（人工待识别 / 人工已出字 /
   引擎块 / 已删除）。`GET /books/{id}/pages/{no}` 现状把 `blocks` 与 `edits` 并列返回、由读取方
   自行合并，区域视图改为服务端合并属**新的读语义**，须先写进 `api.md` 再实施。合并执行归本仓库
**8902**，8900 只透传、不做二次合并（2026-09-29 确认裁决，关闭根壳「由 8900 门面合并」措辞的歧义）。
4. **区域驱动解析＝每框一次单页提交，且必须有退化路径**：管线对已落库区域逐框裁剪页图后调用
   `parse_page`，一个区域出一个块；**不新增模型能力、不改引擎选型**（[ADR 0004](0004-model-boundary-and-pipeline-ownership.md)）。
   未画区域的页与未覆盖区域一律走现状默认检测路径（框图缺失＝现状语义，这是回滚面）。因为每次
   调用独立计 deadline 且都要过单一占用闸门，一页框数必须有上限并在受理前判 422，不让请求打到模型。
5. **调和策略归本仓设计自决**：框间空隙归属、一个框内命中多个引擎块时如何归一、删除/合并意图在
   重解析后按什么规则重新命中区域，由本仓在 `parsing-pipeline.md` 成文（需求方只定义能力与消费侧
   判据）。规则变更受 PP-9 成对约束：不得使既有 `block_index` 编辑静默改指到别的块。
6. **守护与变异复验**：实施轮新增契约断言——页级表唯一键不含 `job_id`、`af_block_edits` 唯一键
   含 `job_id` 不变、区域视图条目数＝人工区域数 + 生效版本块数、区域驱动路径下每框恰好一次
   `parse_page`、无区域时走默认检测；并按 PLAN-016 的纪律逐条变异复验（每次只红对应用例）。
7. **结构操作依赖生效版本（2026-09-29 确认裁决）**：无生效版本指针（即 legacy 根视图作用域）时，
   该页的一切结构操作（add/delete/merge）**一律拒绝**，不以根视图产物作锚定回落；旧版本目录的
   删除仍只发生在激活确认之后（沿用既有激活判据）；解析、版本生命周期与区域投影全部归本仓库
   8902，8900/8903 只读透传、不承载合并或判定逻辑。

## 用户流程表述（2026-09-29，本决定据此确认的原话口径）

用户确认本决定时给出的验收锚点：**① 在前端画好真实需要的解析框；② 按框再进行 OCR 图像解析
（重标）；③ 确认好后固定版本（旧版本删除）存储到数据库；④ 检查中间是否转译（OCR/归一）出错**。
对应落点：①② 即本页级区域表 + 每框一次单页提交（决定 1/4）；③ 即已有的生效指针与激活后删旧
口径（版本生命周期，非本决定新增）；④ 即区域视图（服务端投影，决定 3）+ `af_block_edits`
文字修正覆盖层（决定 2）。三层——**人的意图 / 引擎产物 / 人对结果的判定**——互不覆写。

## 后果

- **正面**：S1/S2 的"改框再解析、删除不复活"类判据变得可实现；人工意图不随解析版本漂移，一次
  画框多次重解析成立；产物正本唯一、元数据入 `af_*`，与根侧 JSON 岛禁令和本仓写入面约束同族；
  `af_block_edits` 既有语义与历史编辑不被牵连。
- **代价**：新增一张表与一次 Alembic 迁移（真实库上的 `upgrade` 属 D 类，须另批窗口）；重解析后
  人工删除/合并意图只能靠几何锚重新命中，存在阈值不确定性，误命中风险要用判据用例钉住；每框一次
  调用的挂钟与占用成本随框数线性增长，与"同一时刻只容一个 MinerU 占用"的既有裁决叠加；
  区域视图的服务端合并是新的读契约，根侧 8903 需同步消费口径。
- **缓解**：退化路径（无区域＝现状默认检测）使回滚只需停端点分支，不动产物；单页框数上限 +
  受理前 422 把成本封顶；实施与自证全部在临时数据根 + SQLite/`qed_test` 上完成，真实库迁移与
  真实数据根写入不进默认门禁；节题/类型类修正（R4/R5）不混进本决定，另按 PP-7/PP-9 判定。
- **多实例与并发画框**不在本决定内（本仓仍是单实例前提，见 [ADR 0007](0007-parse-status-semantics.md)
  的启动期归一条款）；需要时先立取代 ADR。

## 取代

取代：无（本决定为新增边界约束，不改写 ADR 0004/0007/0009 的既有裁决；决定 2 明确保留
`af_block_edits` 现状语义）。
被取代：无。
关联：[ADR 0004](0004-model-boundary-and-pipeline-ownership.md)（模型边界与解析管线归属）、
[ADR 0009](0009-data-root-must-be-explicit.md)（数据根写入面与留档区口径）、
[ADR 0005](0005-mysql-integration-test-isolation.md)（`qed_test` 隔离边界）；
[PP-7/PP-9](../design/parsing-pipeline.md)（块类型与 index 成对约束）、
[PM-3](../design/parsing-management.md)（编辑绑定版本）；实施载体
[PLAN-018](../plans/2026-09-26-parsing-region-edit-typography-request.md)（REQ-099 承接壳）；
跨仓库只读引用：根仓库 ADR 0014（解析能力归属）、根仓库 ADR 0018（数据根白名单与元数据 JSON 岛禁止）、
根仓库 REQ-098/REQ-099。
