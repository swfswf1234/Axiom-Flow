# 解析页区域驱动重解析与块结构编辑承接实施（parsing-region-edit）

状态：In Progress
任务类型：B
最后更新：2026-10-01
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、解析管线归本仓库；R1/R5 改动均在管线与归一化面，不触碰引擎选型）、[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)（**Accepted 2026-09-29**：页框图是页级解析输入、不是版本内覆盖层——决定 R2/R3 的落库作用域与 R1 的调用形态；同批裁决 legacy 拒绝结构操作、合并归 8902）
关联设计：`docs/design/parsing-pipeline.md`（页解析单元与 normalize 块类型）、`docs/design/parsing-management.md`（af_* 块编辑覆盖层，R1/R2/R3 落点）；根仓库权威上下文（只读）：`QED-Engine/docs/plans/2026-09-26-req098-parsing-region-edit-typography-axiom-flow-request.md`（请求原文）、`QED-Engine/docs/design/parsing-ui.md`（消费界面现状）
关联 Tracker：docs/trackers/todo.md（PLAN-018 行；本任务唯一台账行，根侧请求号 REQ-099 只作来源标注，不单独立行）
归档判定：Retain（含 R1~R6 逐条回执与实施锚点，作根仓库 REQ-099 回执关闭证据）

> 本壳为根仓库 REQ-099 跨项目请求（2026-09-26 发出，源自根侧 REQ-098 框图工作台与版式重排
> 实施中的能力缺口）的承接实施计划。原请求文档随承接登记正规化为治理合规的计划形态
> （本文件名保留请求期 slug 以维持根侧引用）。白名单/守护同步：本壳 + todo 单行（PLAN-018）即 PLAN-018
> 承接第一步，`test_plan_governance.py` / `test_tracker_governance.py` 以本壳现状为基准。

## 目标与成功标准

**目标**：本仓库具备「框图=页解析输入」与「块结构持久化编辑」两项当前不具备的能力，
支撑根仓库 8903 框图工作台全量启用；同时修正消费侧实测发现的版面判定缺陷。
需求定义方=根仓库用户，实施形态（表结构/内部设计/引擎调和策略）由本仓库自定。

| ID | 标准（=根侧请求 R1~R6 验收判据，消费侧实测在根仓库执行） |
| --- | --- |
| S1（R1） | 单页解析任务以该页已落库框图为识别单元：画 3 框→解析→恰 3 块且 bbox≈框、文字为框内内容；改框再解析块随框变化 |
| S2（R2） | 块删除/合并/新增区域持久化：PUT 后 GET 即回显新结构，刷新/重启仍在；合并=并集 bbox+阅读序拼接；删除块在合并视图与后续重解析中不复活 |
| S3（R3） | 页数据返回当前生效区域集合（几何含人工修正、关联块或 pending/已删除标记），成为 8903 画布唯一数据源 |
| S4（R4） | 目录页/目录块可被类型或可消费属性区分（低优先，可长线） |
| S5（R5） | b05 重解析后 p5/p7 节题块类型可与正文区分；双栏页右栏块文本非空、行结构完整 |
| S6（R6） | 未完成页能力二择一落地：`skip_parsed=true` 任务仅解析未完成页且已解析页结果不动，或页状态清单视图与真实产物一致（根侧 8903「解析全部未完成页」按钮承接前禁用，为本项硬依赖） |
| S7（确认门） | 页级人工确认态落 `af_pages`（DB 单源）；chunk 重建只聚合已确认页，未确认页在响应中**可见排除**；确认三形态齐备；系统不得自动整本背书 |

**成功标准**：R1~R6 逐条成文回执（接受/改造/拒绝+排期）根仓库；R1~R3 实施达成 S1~S3 并
联调通过；S7 页确认门达成。

## 范围与非目标

- 范围：页解析编排（区域驱动）、normalize 块类型判定（R4/R5）、af_* 块编辑覆盖层扩展
  （R2/R3）、页数据 API 扩展（R3）、页级人工确认门（W8/S7，2026-09-29 自 PLAN-019 P3 移入）、
  相应单测与契约测试、设计文档同步。
- 非目标：不改 OCR/版面引擎选型与模型部署（ADR 0004）；根仓库 8900 门面透传与 8903 画布
  实现归根侧 REQ-098；知识库面/检索链路改动不在本壳；落库位置的存储规范核对按本仓库
  `docs/standards/` 自决，根侧不干预。

## 前置条件

1. **R1 引擎能力勘察已完成（2026-09-26，源码直证）**：本仓引擎协议与提交表单**无任何区域/bbox
   入参**——`engines/base.py` 只有 `parse_document(pdf, filename, start_page, end_page)` 与
   `parse_page(image_bytes, page_no)`，`engines/mineru.py` 的提交字段只含产物开关 `_OUTPUT_FLAGS`
   加窗口 `start_page_id`/`end_page_id`。因此「IoU 调和引擎检测块」满足不了 S1 的「文字=框内取字」
   （引擎漏检的框出不了字），区域驱动解析定为**管线侧框内裁剪、每框一次 `parse_page`**
   （[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 决定 4）。成本必须入契约：每次调用
   独立计 `AXIOM_OCR_TIMEOUT` 且过单一占用闸门 ⇒ 设**单页框数上限**并在受理前判 422，非法请求
   不打模型（与 PLAN-007 的窗口分块同一纪律）。
2. **R2 落库作用域＝页级新表（ADR 0010 决定 1/2）**：`af_page_regions` 唯一键
   `(book_id, page_no, region_id)` 且**不含 `job_id`**；`af_block_edits` 的唯一键与语义保持不变
   （页级=人的意图跨版本，版本级=人对结果的判定不跨版本）。Alembic 迁移须**接在 PLAN-017 的迁移
   合入之后**（判例：并行同链并写必撞版本号）。**2026-09-29 更新**：PLAN-017 迁移已合入，
   现 head 含 `20260926_0001`；按 Q5 裁决，W2 加表动作的前置＝
   [PLAN-020](../history/plans/2026-09/2026-09-29-af-table-family-governance.md)（表族措辞收口）关闭；真实库 `qed` 上的
   `upgrade` 属 D 类，另待窗口批准。
3. **R3 是新的读契约**：现状 `GET /books/{id}/pages/{no}` 把 `blocks` 与 `edits` 并列返回、合并由
   读取方完成（`api/main.py:get_page`）。区域视图要求**服务端投影合并**（人工区域 + 生效版本块 +
   pending/已删除标记），须先改 [api.md](../architecture/api.md) 条文再动实现，并与根侧 8903 的
   消费口径同步。
4. **R4 不新增块类型（判据来自 PP-7/PP-9）**：`BlockType` 固定十种是契约（`schemas.py`），新增
   TOC 枚举要改 `normalize/blocks.py:_TYPE_MAP`，按 PP-9「映射表与产物成对」会使 `index` 语义漂移、
   既有编辑失效。定判：以**可消费属性/分诊标记**承接，归 ARCH-025 滚动壳长线，与 PLAN-009 的 P3
   （blocks 语义分诊 ledger）同批做，不在本壳新增枚举。
5. **R5 取证先行**：节题误判的根因是引擎回 `text` 字面值而归一化侧无启发式（`_TYPE_MAP` 只有
   `title→heading`），修正落在 normalize 且**不改块数故 index 安全**；双栏右栏空文本须先在真实页
   产物上判定是引擎空回（`QualityMetrics.empty_block_ratio` 可见）还是我方丢字（本仓 `order` 恒等于
   `index`、全仓无分栏排序逻辑）。取证只用临时/探针数据根，不占真机批量窗口。
6. **排期与根侧依赖**：2026-09-29 ADR 0010 已确认 ⇒ 本壳转主攻实施位（原挂 PLAN-017 已关闭）；R4/R5 的产物面修正
   需重解析才见效；原「与 ARCH-011 产物重建批量窗口同批出效」的约束随该行 2026-09-30 关闭而
   失效（改判正文＝[PLAN-009](2026-09-22-v1-parsing-knowledge-optimization.md) 承载 ARCH-011 节的「真机取证改挂」表），见效随用户按需解析逐本自然发生。根侧消费端
   （REQ-098 W4/W5）就绪与否不阻塞本壳；R1~R3 承接前根侧按降级口径执行。
## 勘察与裁决登记（2026-09-26）

| # | 结论 | 直证 |
| --- | --- | --- |
| K1 | 引擎侧无区域输入 ⇒ R1 只能管线侧（裁剪逐区域），IoU 调和路线不满足 S1 | `engines/base.py` 协议签名、`engines/mineru.py` 的 `_submit`/`_run` 请求字段 |
| K2 | 现有覆盖层承载不了结构操作：新增区域无版本内下标可绑，PUT 还先判下标越界 | `db/models.py:AfBlockEdit` 唯一键含 `job_id`+`block_index`、`api/main.py:edit_block`、`EditRepository.upsert` 覆盖语义 |
| K3 | 人工框图/删除意图必须跨版本存活，否则 S1「改框再解析块随框变化」与 S2「删除不复活」不可达 | 根侧请求判据 + PP-9 成对约束（`index` 只在版本内有意义） |
| K4 | 新增块类型枚举会漂移 `index` ⇒ R4 走属性不走枚举 | `schemas.py:BlockType`、`normalize/blocks.py:_TYPE_MAP`、PP-7/PP-9 |
| K5 | 区域视图要求服务端合并，现状由读取方合并 | `api/main.py:get_page`、`api.md` GET pages/{no} 小节 |
| 裁决 1 | 用户 2026-09-26：先落文档（勘察结论入壳 + 立 ADR），R2 取**页级新表 + ADR** 路线；ADR 0010 现为 Draft，确认后才动码 | 见本壳「关联 ADR」与上条前置 1~6 |
| 裁决 2 | 用户 2026-09-26：一任务一行——REQ-099 与 PLAN-018 合并为台账单行（由 PLAN-018 镜像行承载，请求号只作来源标注） | todo 规则节与本壳「关联 Tracker」 |

## 用户裁决登记（2026-09-29，ADR 0010 确认 + Q1~Q5 定判）

| # | 裁决 | 影响 |
| --- | --- | --- |
| 流程原话确认 | 用户口径＝验收锚点：**前端画好真实需要的解析框 → 按框再进行 OCR 图像解析（重标）→ 确认好后固定版本（旧版本删除）存储到 DB → 检查中间是否转译出错**；解析与版本部分全部归 8902 | R1~R3/S1~S3 按此流程表述复核（ADR 0010「用户流程表述」节同文） |
| ADR 0010 | 确认，Draft→Accepted | W1/W2 解除「待 ADR 0010 确认」门槛 |
| Q1 | 投影合并归 **8902**，8900 只透传、不做二次合并 | 根壳 `:92`「由 8900 门面合并」措辞归根侧改；草案 D 按原稿晋升 |
| Q2 | `AXIOM_MAX_REGIONS_PER_PAGE`＝**16** 定判 | 进 `config.py`；W1 首次真机按原案计时复校 |
| Q3 | legacy（无生效版本）下 add/delete/merge **一律拒绝**，不以根视图产物作锚定回落；旧版本目录只在激活确认后删除 | 比本壳原建议更严——草案 C 错误段与草案 F 状态码表按此改写；Q3 原「add 允许」的建议作废 |
| Q4 | 下标↔`region_id` 翻译**归本仓**（受理时换算几何锚，响应回 `region_id`） | 草案 C/`BlockLocator` 形状保持，根侧前端后置改造 |
| Q5 | 全仓「四表」措辞 64 处**不随 W2 改**：先另立 [PLAN-020](../history/plans/2026-09/2026-09-29-af-table-family-governance.md)（`af_*` 表族措辞收口 + 守护断言），单独 todo 行评审通过后才允许加表 | W2 与迁移 `20260926_0002` 新增前置＝PLAN-020 关闭 |
| 确认门 | 「每页人工确认门」自 PLAN-019 P3 **移入本壳 W8**（单侧登记，PLAN-019 壳不动，口径以本壳为准） | 判据表新增 S7；工作项新增 W8；ARCH-022 台账行同步注记 |
## 对接口径与契约草案（2026-09-26，Draft 未晋升）

本节是 R1~R3 契约的**唯一承载处**。`architecture/api.md`、`architecture/database-design.md` 与
`design/` 由三条形状守护钉住（见第 8 节）：未挂载的路由进端点总表＝文档虚报，未建的表进列清单＝
对账失败。故全部条文以草稿形态在此评审，评审通过并确认
[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 后按
[ADR 0002](../adr/0002-pending-design-location.md) 随实施轮晋升。
根侧上下文为只读直证：`QED-Engine/docs/plans/2026-09-26-req098-parsing-region-edit-typography.md`
（下称**根壳**）与 `QED-Engine/docs/architecture/api-contracts.md`。

### 1. 两侧接口对账（现状 → 目标）

| 对接面 | 根侧口径（只读直证） | 本仓现状（直证） | 结论 |
| --- | --- | --- | --- |
| 读页区域集合 | 根壳 `:92`：`GET /books/{id}/pages/{no}` 扩展 `regions` 视图字段，「由 8900 门面合并 blocks 与结构编辑 overlay 生成」 | `PageData`（`schemas.py:457`）返回 `blocks`+`edits` 并列，合并由读取方完成（`api/main.py:396 get_page`，作用域唯一入口 `_read_scope`＝`api/main.py:197`） | URL 与既有字段零变化、只增 `regions`（向前兼容）；**合并位置两侧口径冲突**，见 Q1 |
| 块结构编辑 | 根壳 `:93`：`PUT /books/{id}/pages/{no}/structure`（body：delete/merge/add-rect），`:153` 由 8900 透传并在 `api-contracts.md:662` 登记 | 无此端点；结构意图现只能绑版本内下标（`api/main.py:427 edit_block`、`db/models.py:115 AfBlockEdit` 唯一键含 `job_id`） | 新增端点（草案 C）；未挂载时 PUT 即 404，与根壳 `:140` 降级判据（404/501）一致 |
| 区域驱动解析 | 根壳 `:94`：`POST /parse-jobs {pages:[n]}`，「请求体是否显式带 regions 参数由 R1 方案定（对方侧实施选择）」 | `ParseJobCreate`（`schemas.py:469`）只有 `book_id`/`pages`/`engine`，`job_id` 服务端生成 | **两步式、不新增请求体字段**：框图先经 `PUT …/structure` 落 `af_page_regions`，提交时读该页已落库框图（避免 UI 状态与任务入参两处真相），符合根壳 `:94` 授权 |
| 坐标系 | 根侧 `web-ui/src/components/parsing/regions.ts` 负责换算；`parsing-ui.md:119-123` `regionDraft{added:BBox[], deleted:number[], merged:number[][]}` | `BBox`＝`[x0,y0,x1,y1]` 原页图像素（`schemas.py:131`），基准 `pages/p<NNNN>.png`（`api.md:290-293`） | 两侧同基准，**无需换算层**；根侧 `deleted`/`merged` 用块下标 ⇒ 跨版本存活靠几何锚，见草案 A 与 Q4 |
| 单页框数上限 | 未声明 | 引擎协议无区域入参（`engines/base.py:78-92`、`engines/mineru.py:335-349`）⇒ 每框一次单页提交，各吃 `AXIOM_OCR_TIMEOUT` 并各过一次闸门 | 上限进契约并受理前 422（草案 E）；取值见 Q2，定判后回执告知根侧做前端预检 |

### 2. 草案 A：`af_page_regions`（页级区域与结构意图表）

列类型范式对齐 `db/models.py:98-138`（`String(100)` book_id / `Integer` page_no / `JSON` 几何 /
`DateTime` 时间戳）。

| 列 | 类型 | 约束 | 语义 |
| --- | --- | --- | --- |
| `book_id` | String(100) | PK | 同 `af_books.book_id` |
| `page_no` | Integer | PK | 1 基页码 |
| `region_id` | String(64) | PK | 区域稳定身份（服务端生成，跨版本不变；响应回给根侧长期定位用） |
| `op` | String(16) | NOT NULL，default `add` | `add`（人工新框）/ `delete`（删除某块）/ `merge`（合并多块） |
| `bbox` | JSON | nullable | `op=add` 必填：人工框几何（原页图像素） |
| `anchor_bbox` | JSON | nullable | `op=delete` 必填、`op=merge` 填并集：操作对象在**受理时生效版本**产物中的几何快照，重解析后按 IoU 重定位 |
| `members` | JSON | nullable | `op=merge` 必填：参与合并的定位列表（含阅读序） |
| `kind` | String(24) | NOT NULL，default `''` | 区域用途提示（R4 的属性路线落点，**不扩 `BlockType`**） |
| `note` | String(1000) | NOT NULL，default `''` | 备注 |
| `created_at` / `updated_at` | DateTime | NOT NULL | 时间戳 |

索引 `ix_af_page_regions_page (book_id, page_no, op)`；唯一性由三列主键承载。

**不变式（须与列清单同批进 database-design）**：本表**无 `job_id` 列**——区域是页级解析输入
（[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 决定 1），与 `af_block_edits`（版本级
人工判定，唯一键含 `job_id`、PP-9）正交：前者跨版本存活人的意图，后者不跨版本。

迁移：`20260926_0002_page_regions`，`down_revision = '20260926_0001_ingest_metadata_columns'`
（现 head，`src/axiom_flow/migrations/versions/`）。真实库 `qed` 上的 `upgrade` 属 D 类，另待窗口批准。

### 3. 草案 B：`schemas.py` 新模型

```python
class RegionOp(StrEnum):
    # af_page_regions.op：页级结构意图三态
    ADD = "add"
    DELETE = "delete"
    MERGE = "merge"


class RegionState(StrEnum):
    # 区域在合并视图中的呈现态（读时派生，不落库）
    ACTIVE = "active"          # 生效版本块，无人工干预
    MANUAL = "manual"          # 人工新框、尚未解析出块（pending）
    PARSED = "parsed"          # 人工框已解析出块
    DELETED = "deleted"        # 已删除块（视图保留标记，重解析后不复活）
    MERGED = "merged"          # 参与合并的源块（视图折叠到合并块）
    UNANCHORED = "unanchored"  # 几何锚定失败——可见暴露，不静默丢弃（ADR 0004 无自动兜底）


class BlockLocator(BaseModel):
    # 结构意图的定位入参：三选一，服务端受理时统一换算为几何锚
    block_index: int | None = Field(default=None, ge=0)  # 仅当前生效版本内有意义
    region_id: str | None = None
    bbox: BBox | None = None


class MergeGroup(BaseModel):
    targets: list[BlockLocator] = Field(min_length=2)  # 数组序即阅读序


class StructureRequest(BaseModel):
    # PUT …/structure 请求体：该页结构意图的**全量声明**（缺席即撤销，天然幂等）
    adds: list[BBox] = Field(default_factory=list)
    deletes: list[BlockLocator] = Field(default_factory=list)
    merges: list[MergeGroup] = Field(default_factory=list)


class RegionView(BaseModel):
    region_id: str
    state: RegionState
    source: Literal["engine", "manual"]
    bbox: BBox
    block_index: int | None = None      # pending/manual/deleted 视态可为 None
    block_type: str = ""
    text: str | None = None             # 截断预览；完整文本仍走 blocks
    merged_from: list[str] = Field(default_factory=list)
    kind: str = ""
```

`PageData`（`schemas.py:457`）追加 `regions: list[RegionView] = Field(default_factory=list)`；
`blocks` / `markdown` / `image_url` / `edits` 字段与顺序不变。

### 4. 草案 C：`PUT /api/v1/books/{book_id}/pages/{page_no}/structure`

晋升时作为新 `####` 小节插入 `api.md` 的 `#### PUT …/blocks/{index}/edit` 与 `#### GET …/edits`
之间，并在端点总表（`api.md:90-115`）同批加一行。条文草稿：

> 页结构意图**全量声明**（落 `af_page_regions`，页级、不绑生效版本）。请求：
> `{adds?: [[x0,y0,x1,y1],…], deletes?: [{block_index?|region_id?|bbox},…], merges?: [{targets:[locator,…]（≥2）},…]}`；
> **缺席即撤销**：本次未列出的既有人工意图被删除，重复提交同一组意图结果不变（幂等，无需 undo 端点）。
> 响应：该页重算后的 `RegionView[]`（与 `GET …/pages/{page_no}` 的 `regions` 同构）。
>
> **定位与锚定**：`block_index` 只在当前生效版本内有意义，服务端受理时即换算为该块几何快照写
> `anchor_bbox`；重解析后按 IoU 阈值重定位（阈值与框内多检测块的归属规则写 `design/parsing-pipeline.md`）。
> 锚定失败的意图不静默丢弃，在视图中以 `state=unanchored` 暴露。
>
> **错误**：400 定位三项全缺 / `merges.targets` 少于 2 / bbox 形状非法（x1≤x0 或 y1≤y0）/
> 下标越界 / 意图总数超 `AXIOM_MAX_REGIONS_PER_PAGE`；404 书目或页不存在 / 生效版本产物缺失
> （无从锚定，**不回落根视图**，与读取面同码）/ legacy 无生效版本 ⇒ add/delete/merge
> 一律拒绝（Q3 定判 2026-09-29，**add 亦拒**，草案 Q3 原「add 允许」建议作废）；422 `book_id` 非法路径段。
> 本端点**不依赖模型服务**（只写覆盖层，随时可调）。

### 5. 草案 D：`GET /api/v1/books/{book_id}/pages/{page_no}` 的 `regions` 字段

`api.md:277-285` 的 JSON 例追加：

```json
{
  "blocks": { "...": "现状不变" },
  "markdown": "...",
  "image_url": "/api/v1/books/{book_id}/pages/{page_no}/image",
  "edits": [ { "...": "现状不变" } ],
  "regions": [
    {"region_id": "rgn_01", "state": "active", "source": "engine", "block_index": 0,
     "bbox": [72, 96, 540, 132], "block_type": "paragraph", "text": "…", "merged_from": [], "kind": ""},
    {"region_id": "rgn_02", "state": "manual", "source": "manual", "block_index": null,
     "bbox": [72, 900, 600, 1010], "block_type": "", "text": null, "merged_from": [], "kind": ""},
    {"region_id": "rgn_03", "state": "deleted", "source": "engine", "block_index": 5,
     "bbox": [600, 96, 1100, 210], "block_type": "figure", "text": null, "merged_from": [], "kind": ""}
  ]
}
```

追加句：**`regions` 为服务端投影合并结果**（人工区域 + 生效版本块 + pending/已删除/合并态标记），
是 8903 画布的唯一数据源；`blocks` 仍是产物原样（不做就地删改），二者同源同作用域（`_read_scope`）。
新增字段向前兼容：未知字段可忽略，`image_url` 字面量常量不变。

### 6. 草案 E：`POST /api/v1/parse-jobs` 补句（区域驱动）

插在 `api.md:336-338`「受理前门槛」段之后：

> **区域驱动（该页有已落库人工框时）**：`pages` 指定的每一页以 `af_page_regions` 当前框为识别单元，
> 管线侧框内裁剪、每框一次单页提交（ADR 0010 决定 4），一框一块。**请求体不新增区域字段**——框图
> 必须先经 `PUT …/structure` 落库，提交时由服务端读取。人工框总数超 `AXIOM_MAX_REGIONS_PER_PAGE`
> 时**受理前 422**（每框独立计 `AXIOM_OCR_TIMEOUT` 且各过一次闸门，成本必须可见；与 PLAN-007 窗口
> 分块同一纪律）；无人工框的页回落引擎默认检测（现状路径，退化为非区域驱动）。

`ParseJobCreate` 无字段变化 ⇒ 现有契约测试与 8900 透传形状不受影响。

### 7. 草案 F：`api.md` 状态码总表补行（`:503-518`）

| 场景 | 状态码 |
| --- | --- |
| structure：定位三项全缺、合并成员少于 2、bbox 形状非法、块下标越界、意图总数超上限 | 400 |
| structure：生效版本产物缺失（无从锚定） | 404（与读取面同码，**不回落根视图**） |
| structure：legacy 无生效版本（任何结构操作，含 add） | 404（Q3 定判 2026-09-29） |
| parse-jobs：该页人工框数超 `AXIOM_MAX_REGIONS_PER_PAGE` | 422（受理前判，不打模型、不落任务行） |
| `PUT …/structure` 未挂载（承接实施前） | 404（与根侧降级判据一致：按钮禁用 + tooltip） |

### 8. 晋升路径与三条守护约束

1. `tests/contract/test_fixed_document_form.py:test_api_endpoint_table_matches_runtime_routes`
   把 `api.md` 端点总表与 `app.routes` 逐行对账（表多于路由＝文档虚报），
   `test_api_endpoint_sections_match_table` 要求每个总表行有同名 `#### METHOD path` 小节
   ⇒ 草案 C 的小节与总表行只能与**路由挂载同批**晋升。
2. `test_column_list_matches_orm_columns` 把 `database-design.md` 的「### <table> 列清单」与
   SQLAlchemy 列双向对账（含可空、主键、VARCHAR 长度），`test_table_constraints_and_indexes_are_documented`
   对约束与索引 ⇒ 草案 A 的列清单只能与 `AfPageRegion` ORM + 迁移**同批**晋升。
3. `test_architecture_documents_follow_four_section_skeleton`（H2 必须等于固定骨架，**不得新增小节**）
   与 `test_architecture_body_carries_no_process_narrative` / `test_design_body_carries_no_process_narrative`
   （正文禁任务编号与绝对日期）⇒ 本节文本**不得整段搬入** `architecture/` 与 `design/`，晋升时必须
   改写成稳定口径（去掉 PLAN/REQ/S/Q 编号与日期），过程性内容留在本壳与 trackers。

结论：本节先评审后晋升——W2（表）与 W3（读视图）、W1（区域驱动）各自以「条文 + 路由/ORM + 契约测试」
在同一变更内落地，禁分批（否则任一守护必红）。

### 9. 待定口径（2026-09-29 已全部裁决定判，见「用户裁决登记」节；本表保留原悬置记录）

| # | 悬置问题 | 建议与理由 |
| --- | --- | --- |
| Q1 | 合并发生在 8902 还是 8900（根壳 `:92` 写「由 8900 门面合并」，本仓 ADR 0010 决定 3 写服务端投影合并） | **8902**。区域投影同时要读生效版本产物 `blocks` 与 `af_page_regions`，两者只在 8902 侧可得；8900 只做透传，否则合并规则两处漂移。裁定后根侧改 `:92` 措辞，并把 `parsing-ui.md` 的合并口径段改指 8902 响应 |
| Q2 | `AXIOM_MAX_REGIONS_PER_PAGE` 取值 | 草案 16。判据：框数 × 单框 `AXIOM_OCR_TIMEOUT` + 闸门等待（900s 上限）须落在一个真机窗口可接受时长内；取值随 W1 首次真机计时复校 |
| Q3 | 无生效版本指针（`af_books.active_job_id` NULL = legacy 根视图）时的结构操作 | `add` 允许（不依赖产物）；`delete`/`merge` 需锚定生效块。legacy 作用域**有**根视图产物且 `GET pages/{no}` 可读 ⇒ 建议允许锚定（与读面同构，避免「能看不能改」），仅版本自证类端点（manifest）在 legacy 维持 404 |
| Q4 | 下标 ↔ `region_id` 翻译归谁 | **归本仓**（草案 C）：接受根侧现有 `deleted:number[]`/`merged:number[][]` 下标形态作为定位入参，落库时换算为几何锚；响应回 `region_id`，根侧前端可后置改造 |
| Q5 | `af_*` 表数 4→5 的措辞同步面（见第 11 节，非归档面 64 处）是否随 W2 同批 | 建议**先收口措辞再加表**：散文改称「`af_*` 表族」、张数只留在三处权威口径并加守护，否则每次加表都要改 60+ 处。同批做＝一个大 diff；分批做＝W2 前先立一行治理支线 |

### 10. 根侧待办（随 S-7 回执一并交付）

- `QED-Engine/docs/architecture/api-contracts.md:662`「规划契约（8902 已实现、8900 透传待实施）」表
  （`:665-671`）新增两行：`PUT /books/{book_id}/pages/{page_no}/structure`、
  `GET /books/{book_id}/pages/{page_no}` 的 `regions` 字段。端点总表（`:130-145`）只在 8900 透传
  落地后加行——受根侧 `tests/contract/test_api_endpoint_inventory.py` 门禁约束，与本仓第 8 节第 1 条同理。
- 8900 门面：`…/structure` 透传 + `pages/{no}` 原样透出 `regions`（依 Q1 不做二次合并）。
- `QED-Engine/web-ui/src/api/axiom.ts`：加 structure PUT；`getPage` 消费 `regions`；
  `parsing-ui.md:119-123` 的 `regionDraft` 由「落库禁用」转「接通」，同步 `:174`、`:323`、`:326`
  三处降级措辞。
- 根壳 `:92`/`:93`/`:94` 三行按 Q1 与草案 E 校准（`:94` 的「请求体是否显式带 regions 参数由 R1 方案定」
  现已定判：**不带**，走两步式）。
- 降级判据保持不变（根壳 `:140`）：本仓端点未挂载时 PUT 返 404 ⇒ 前端按钮禁用 + tooltip。

### 11. `af_*` 表数 4→5 的同步面（新增治理成本）

`AF_TABLES`（`src/axiom_flow/db/models.py:141`）由四元组扩为五元组，牵连全仓「四表」措辞。非归档面
命中 **64 处 / 28 文件**（`rg -c 四表 src tests scripts docs/architecture docs/design docs/standards docs/guides docs/adr`），
另 `docs/history/`（32 处）与 `docs/trackers/`（22 处）是当时记录，**不改写**。重点面：

- 隔离口径：`docs/adr/0005-mysql-integration-test-isolation.md`（5）、`docs/standards/testing.md`、
  `tests/support/mysql.py` —— ADR 0005 的「只可 DROP/CREATE 本方自有 `af_*` 表」白名单必须随新表更新。
- 架构：`database-design.md`（5）、`api.md`（7）、`overview.md`（4）、`code-map.md`（2）。
- 设计与运维：`design/parsing-management.md`（2）、`guides/operations.md`（5）。
- 代码/测试/迁移：`db/__init__.py`（2）、`migrations/versions/20260917_0001_create_af_tables.py`（2）、
  `tests/integration/test_parse_persistence.py`（3）、`tests/unit/test_db.py`（2）、
  `tests/unit/test_db_migrations.py`（2）、`tests/contract/test_api_v1_contract.py`（1）。

处置建议见 Q5（把张数从散文收进守护断言：白名单 == `AF_TABLES`，加表即改一处）。

## 工作项


| # | 工作项（=根侧请求 R1~R6 + 回执） | 状态 |
| --- | --- | --- |
| W0 | 承接登记：本壳正规化 + todo 单行（PLAN-018，证据列带 **请求接收（根仓库）** 标记并注根侧请求号 REQ-099），契约测试绿 | 完成 2026-09-26 |
| W0.5 | 源码勘察（K1~K5 入壳）+ [ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 立稿：区域=页级解析输入、每框一次单页提交、R4 不新增枚举 | 完成 2026-09-26（ADR 待用户确认） |
| W0.6 | 对接面梳理与契约草案入壳（本节：两侧对账 + 草案 A~F + 晋升守护 + Q1~Q5 待定口径 + 表族同步面） | 完成 2026-09-26（草案待用户审；**未晋升** architecture/ 与 design/） |
| W1（R1） | 区域驱动页解析：按前置 1 逐框裁剪提交 `parse_page`，一框一块；含单页框数上限与受理前 422、无框时的默认检测退化路径；调和策略（空隙归属、框内多检测块归一）写入 `parsing-pipeline.md` | ADR 0010 已确认 ⇒ 待开工（依赖 W2 表） |
| W2（R2） | `af_page_regions`（页级、不绑 `job_id`）+ 删除/合并/新增区域 API；`af_block_edits` 语义不动；迁移 `20260926_0002` 接现 head 之后 | 完成 2026-10-01（前置：PLAN-020 于 2026-09-29 关闭放行建表，以其「新表结构与表族关联」节为准。交付面、守护、状态码定判与偏差登记见「W2 落地结论与偏差登记」节；业务库迁移待 D 类窗口） |
| W3（R3） | 页数据返回服务端投影的区域集合（几何/来源/pending/已删除标记）；先改 `api.md` 条文再实施 | 待开始 |
| W4（R4） | 目录以**可消费属性/分诊标记**承接（不新增 `BlockType`），归 ARCH-025 与 PLAN-009 P3 长线 | 长线（移出本壳实施） |
| W5（R5） | 节题判定启发式修正（归一化侧，index 安全）＋双栏取字完整性：先取证定位根因再定层面 | 待取证 |
| W7（R6） | 未完成页能力（根侧 2026-09-28 增补）：`skip_parsed` 参数或页状态清单视图二择一，形态本仓定；与 W1 任务面同批裁决（`ParseJobCreate` 现仅 book_id/pages/engine） | 待开始 |
| W8（确认门） | 每页人工确认门（2026-09-29 裁决自 PLAN-019 P3 移入本壳，单侧登记）：确认态落 `af_pages`（DB 单源）、chunk 重建只聚合已确认页、三形态齐备（单页/页区间/按页重解析回未确认）、写入走管理面 token 口径（判例同 PLAN-010）、系统不得自动整本背书 | 待开始（契约草案随 W3 轮） |
| W6 | 回执根仓库 REQ-099：逐条接受/改造/拒绝 + 排期成文；实施后同步锚点（commit sha）、门禁读数与 S 判据逐条判定 | 待开始 |

## W2 落地结论与偏差登记（2026-10-01）

结论：**W2 已实施并通过本地门禁**——第五张表 `af_page_regions`（页级、PK 不含 `job_id`）+ 迁移
`20260926_0002_page_regions.py` + `PUT /api/v1/books/{book_id}/pages/{page_no}/structure` 结构意图
全量声明端点。W2 只交付**写入面**（意图落库与三选一 → 几何锚换算），区域投影读语义（`regions`）与
区域驱动提交分别留给 W3、W1。业务库 `qed` 的 `20260926_0002` 属 D 类窗口，本批**未执行**。

### 交付面与守护

| 交付 | 位置 | 守护（会红的判据） |
| --- | --- | --- |
| 页级区域表与 ORM | `src/axiom_flow/db/models.py`（`AfPageRegion`、`AF_TABLES`） | `tests/unit/test_db_page_regions.py`（无 `job_id` 不变式、全量声明的缺席即撤销、`region_id` 确定性派生的幂等、按书/整表清除）、`tests/unit/test_db_table_family_governance.py` |
| 迁移 | `src/axiom_flow/migrations/versions/20260926_0002_page_regions.py` | `tests/integration/test_parse_persistence.py`（真 MySQL 升级到当前 head + 表族逐名对账） |
| 区域仓储 | `src/axiom_flow/db/repositories.py`（`RegionRepository`/`RegionIntent`/`region_id_for`） | 同上单测；意图行由 api 侧换算后一次 `replace_page` |
| 结构声明端点 | `src/axiom_flow/api/main.py`（`put_structure`） | `tests/contract/test_api_v1_contract.py::TestStructureEndpoint`（14 例） |
| 删除面跟随 | `src/axiom_flow/api/admin.py`（purge 逐表） | 表族治理契约测试 + `TestStructureEndpoint` 的删书带走用例 |
| 文档同步 | `database-design.md`（列清单 + 迁移行 + 表清单第 5 行）、`api.md`（③ 类小节 + 端点总表 + 状态码总表）、`design/parsing-management.md`（PM-18 守则 + 端点语义 + 生命周期图 + 判据 6）、ADR 0005 白名单、ADR 0010 落地事实、`code-map.md`、`overview.md` 能力矩阵 | `test_fixed_document_form.py`（表↔路由↔小节、列清单↔ORM）、`test_architecture_documents.py`、`test_design_documents.py`（守则编号连续＋承载列非空）、`test_code_document_mapping.py` |

门禁读数（本批收口时复跑）：`pytest tests -q` 591 passed / 10 skipped；`ruff check src tests` 全过；
`QED_TEST_MYSQL=1 pytest tests/integration -q` 8 passed（含新迁移在真 MySQL 的升级）。

### 状态码归属定判

采**草案 C/F 原文**（用户已确认的晋升文案）：

- 意图条数超 `AXIOM_MAX_REGIONS_PER_PAGE` ⇒ **400**，判在落库前、零落库；
- `page_no` 小于 1 或越出 `af_books.page_count` ⇒ **404 页不存在**（与书目/产物缺失同码），受理前判；
- 定位违规（三选一给多或给少、`region_id` 不在本页、`bbox` 形状非法、块下标越界、合并成员少于 2）⇒ 400；
- legacy 无生效版本 ⇒ 404 一律拒绝（ADR 0010 决定 7）。

[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 决定 4 的「受理前判 422」作用域是**提交面**
（`POST /api/v1/parse-jobs` 的框数上限，防请求打到模型），随 W1 落；本端点不调模型服务，不占该档。
实施中一度把上述两项误判成 422（把决定 4 当成了全链路口径），已回退并同步 `api.md` 端点小节、
状态码总表两行与两条契约用例。

### 偏差登记（与草案 / PLAN-020 壳的差异）

| # | 差异 | 定判理由与落点 |
| --- | --- | --- |
| D1 | 响应形状为 `RegionRecord[]`（意图行原样），草案 C 写 `RegionView[]` | `RegionView` 是「人工意图 + 生效版本块」的投影合并视图，Q1 已裁归读语义（W3 / 8902 消费）；W2 只交付写入面，`api.md` 明写「另一条**读语义**」 |
| D2 | `merge` 不落 `anchor_bbox`，成员几何唯一落 `members`，不存并集列 | 单一事实源：并集是 `members` 的派生值，双写在成员改判时必漂移（PLAN-020 壳「merge 填并集」不采纳）。成文于 `database-design.md` 区域表语义段与 `api.md` |
| D3 | `MergeGroup.targets` 不加 pydantic `min_length=2`（草案 B 写法），少于 2 由端点判 400 | 「成员少于 2」是跨资源语义违规而非请求体形状违规，与其余定位违规同档，detail 指到原因 |
| D4 | 状态码归属采草案 C/F（400 超上限 / 404 页不存在），非 422 | 见上一小节；`api.md` 端点小节与状态码总表、`TestStructureEndpoint` 两条用例同批 |
| D5 | admin 删除面常量改名 `_AF_TABLE_MODELS`（原 `_FOUR_TABLES`） | PLAN-020 晋升守则「表张数不是散文事实」：名称随表族派生，并有禁逐字表名守护 |
| D6 | 集成层把迁移 head 断言由写死 `20260926_0001` 改为 `_head_revision()` 派生 | 写死序号在加迁移时假红/假绿；派生后「夹具漏跑最新迁移」才是它抓的漂移 |
| D7 | 业务库 `20260926_0002` 未执行，head 停在 `20260926_0001` | D 类窗口按用户排期执行；`database-design.md` 迁移现状段、`api.md`/ADR 0010 元数据、`overview.md` 能力矩阵四处一致标注「待真机窗口」 |

### 遗留（不在 W2 面）

- W3：`GET …/pages/{page_no}` 的 `regions` 投影、重解析后的 IoU 重锚定与框内多块归属规则（写
  `design/parsing-pipeline.md`，ADR 0010 决定 5 要求本仓自决成文）。
- W1：区域驱动提交 + 每框一次单页提交 + 提交面 422 + 无框退化路径；W7 未完成页能力与之同批裁决。
- W8/S7：每页人工确认门（验收锚点的「确认好后固定版本」环节）。

## 验证与验收


- TDD：每项先红后绿；区域驱动解析与结构编辑的契约测试进 `tests/contract/`；
  单测只用临时目录，禁读写真实数据根（ADR 0009）。
- 回归：`pytest tests -q` + `ruff check src tests` 全量门禁。
- S1~S6 消费侧判据实测由根仓库经 8900→8902 链路取证，不属本壳门禁；本壳以 API 契约
  测试与产物对账用例为验收面。

## 回滚

- W2 为新表 + 新端点、W3 为读视图扩展：都不触碰引擎原始产物，回滚即停用相应端点与区域投影逻辑（新表留空即可，不做有损 downgrade）。
- W1 若改解析编排：以「框图缺失=现状默认检测」为退化路径，回退解析任务入口分支即可。
- Alembic 迁移附带 downgrade；已产生的结构编辑数据在覆盖层内，不污染原始块产物。

## 关闭与归档

- 关闭条件：R1~R6 逐条回执成文（W6）且 R1~R3 达成 S1~S3 联调通过、S7 确认门达成；
  R4/R5 若判长线，
  回执注明后转 ARCH-025 滚动壳跟踪，不阻塞本壳收口。
- 按 ADR 0006 六步收口（结论入本壳→todo 单行关闭→completed.md→plans/index 无需登记
  （索引导航制）→project-status 刷新→门禁全绿）。
- 归档判定 Retain：本壳随回执归档（根侧 REQ-099 关闭证据引用其回执与锚点）。
