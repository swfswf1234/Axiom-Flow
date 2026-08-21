# 计划 2026-08：模型模式与 MinerU 网关轮

状态：Accepted
任务类型：B
最后更新：2026-08-20
关联 ADR：无（随根仓库 LLM 网关设计执行；实施验证后按需沉淀 ADR）
关联设计：`docs/design/model-mode-config.md`（本计划将其 Draft 定稿为 Accepted）、`docs/design/pdf-parsing-and-rendering.md`、`docs/design/8902-integration-contract.md`
关联 Tracker：`docs/trackers/todo.md`（MODEL-001 Plan 行镜像本计划；V2-014、V2-005、V2-006）
归档判定：关闭后按文档规范选择性保留或归档至 `docs/history/` 对应分类

## 目标与成功标准

- 目标：Axiom-Flow 模型调用形态对齐三项目统一约定（QED-Tracker REQ-043 先例）：
  1. **V2-014**：新增 `config.py`（自身 `.env` → 根 `.env` 兜底，不污染 os.environ）、
     `llm_client.py` 双模式兼容层（`local` 直连 qwen-vl / `qed-engine` 经 8900 网关调 MinerU）、
     服务脚本 `--mode local|qed-engine`（重启可换模式）、MinerU 编排移交根仓库
     `scripts/image-model/`；
  2. **V2-005 重新做**：qed-engine 模式 MinerU 经网关最小闭环（PDF → 全文 markdown →
     页级 md/blocks 落盘）；
  3. **V2-006 取消**：不用兜底（用户裁决 2026-08-20），服务按当前模式用单一图像模型。
- 成功标准：
  1. local 模式直连 qwen-vl 真实冒烟 5 页跑通（`AXIOM_VISION_MODEL=qwen-vl-plus`）；
  2. qed-engine 模式经 8900 网关 MinerU 真实冒烟 5 页跑通（根仓库 8900 `--mode local` +
     MinerU 容器在线）；
  3. `restart --mode` 双向切换生效，`status` 显示模式；
  4. local 模式直连成功写 `qed_llm_calls`（`service=axiom_flow`，DB 不可达降级不抛）；
  5. 本仓库 MinerU 编排脚本（compose.yaml/infra-*.ps1/docker/smoke-api.sh）已移除；
  6. 全量门禁 `pytest tests -q` + `ruff check src tests scripts` 全绿。

## 范围与非目标

- 范围：
  - 阶段 0 台账清理（completed.md 迁移、V2-010/DOCS-001 计划归档、V2-006 取消）；
  - V2-014（config.py、llm_client.py、service --mode、.env、设计文档定稿、MinerU 移交、
    文档同步）；
  - V2-005 最小闭环（qed-engine 模式 MinerU 经网关：分页 spike、markdown 切分落盘、
    parse-jobs 按模式路由、双模式冒烟）；
  - 收口（todo/code-map 同步、门禁、回执 REQ-044）。
- 非目标：
  - V2-004 编排深化（state.sqlite、后台任务、质量信号）——顺延后续轮；
  - V2-006 兜底链路——取消；
  - 本仓库不直接操作 MinerU 容器（编排归根仓库 `scripts/image-model/`）；
  - 不写根仓库 / QED-Tracker 任何文件（只读参照）。

## 前置条件

- 工作树在 `release` 分支，保留用户已有变更（todo.md / design/index.md 已加 V2-014 行、
  model-mode-config.md Draft 未跟踪）；
- 根仓库侧已落位（2026-08-20）：`scripts/image-model/`（compose.yaml/infra-*.ps1/
  docker/Dockerfile/qed_mineru_service.py）、8900 `/api/v1/llm/vision` 网关、
  `qed_llm_calls` 表迁移；
- 本地 `.env` 存在（gitignored）：`API_KEY` 迁入现 `AXIOM_API_KEY` 值；
- 冒烟前置（执行时向用户确认环境就绪）：8900 服务在线（local 模式）+ MinerU 容器；
- 门禁：`pytest tests -q` + `ruff check src tests scripts`。

## 工作项

### 阶段 0：台账清理

| 项 | 内容 | 验证 |
| --- | --- | --- |
| S0.1 | `docs/trackers/completed.md` 追加 V2-001/002/003/012（终态 Completed、关闭结果、证据）；todo.md 移除对应行 | 契约测试 `test_tracker_governance.py` |
| S0.2 | V2-011 关闭：回执根仓库 REQ-039（提交 `f5d9355`，回执文本交给用户登记）；completed.md 追加；todo 移除 | 同上 |
| S0.3 | V2-010 关闭 Achieved：`docs/plans/2026-08-qwen-vl-plus-trial.md` 归档 `docs/history/plans/2026-08/`（证据保留）；completed.md 追加 | 同上 |
| S0.4 | DOCS-001 关闭：`docs/plans/2026-08-v2-docs-rebuild.md` 归档 history；completed.md 追加 | 同上 |
| S0.5 | V2-006 取消：completed.md 追加（终态 Cancelled，证据=用户裁决 2026-08-20）；todo 移除 | 同上 |
| S0.6 | todo.md 新增本计划 Plan 行（类型=Plan、状态=Accepted、链接计划文档） | `test_plan_governance.py` + `test_tracker_governance.py` |

### V2-014（阶段 1）

#### Task 1：`src/axiom_flow/config.py` + `database.py`（对齐 QED-Tracker）

**文件**：
- 创建：`src/axiom_flow/config.py`、`tests/unit/test_config.py`
- 创建：`src/axiom_flow/database.py`（mysql_url / create_engine_for / utc_now）
- 修改：无（后续任务接线）

- [ ] **Step 1：写失败测试** `tests/unit/test_config.py`：
  - `load_settings` 优先级：真实 env > 自身 `.env` > 根 `.env`（向上走查）> 内置默认；
  - 不修改 `os.environ`（自身 `.env` 读取后 os.environ 无 `QED_*`/`API_KEY` 残留）；
  - `llm_api_key()` 只读 `API_KEY`；
  - 空值键跳过（`API_KEY=` 留给兜底来源）；
  - `Settings` 密钥字段不进 repr；
  - `data_dir` 语义：`AXIOM_FLOW_DATA_DIR` > `AXIOM_DATA_DIR` > 仓库 `data/`。
- [ ] **Step 2：运行确认失败**：`pytest tests/unit/test_config.py -q`
- [ ] **Step 3：实现 config.py**（参照 `QED-Tracker/src/qed_tracker/config.py` 结构）：
  - `Settings` dataclass：`data_dir`、`api_select`（`QED_API_SELECT`，默认 `local`）、
    `llm_gateway_url`（默认 `http://127.0.0.1:8900`）、`vision_model`（`AXIOM_VISION_MODEL`，
    默认 `qwen-vl-plus`）、`db_host/port/name/user/password`（`QED_DB_*`，默认 qed 库）、
    `port`（`AXIOM_PORT`）；
  - `_env_file_values(start)`：向上走查 `.env`，键限 `QED_*`、`API_KEY`、`AXIOM_*`（私有），
    不写 os.environ；
  - `_env_value(key)`：真实 env > 文件值；
  - `load_settings(**overrides)`、`llm_api_key()`；
- [ ] **Step 4：运行确认通过**
- [ ] **Step 5：database.py**（`create_engine_for(settings)` / `mysql_url` / `utc_now`，
  参照 `QED-Tracker/src/qed_tracker/database.py`），单测轻量（utc_now naive、mysql_url 拼装）
- [ ] **Step 6：提交**：`git add src/axiom_flow/config.py src/axiom_flow/database.py tests/unit/test_config.py && git commit -m "feat(config): 对齐 QED-Tracker 引入 config/database 层（V2-014）"`

#### Task 2：`src/axiom_flow/llm_client.py` 双模式 + fallback 改造

**文件**：
- 创建：`src/axiom_flow/llm_client.py`、`tests/unit/test_llm_client.py`
- 修改：`src/axiom_flow/fallback/client.py`（凭据读 config/`API_KEY`、模型读
  `AXIOM_VISION_MODEL`；`VisionError` 保持导出）、`src/axiom_flow/fallback/__init__.py`
  （导出保持兼容）、`tests/unit/test_fallback.py`

- [ ] **Step 1：写失败测试** `tests/unit/test_llm_client.py`：
  - `VisionClient(api_select="local")`：direct 路径——mock transport 直连 dashscope
    `/chat/completions`（image base64 data URI + PROMPT），成功返回 markdown；
    失败（429/5xx 重试、4xx、空响应）语义复用 fallback 客户端行为；
  - direct 成功调用写 `qed_llm_calls`（mock engine：`service=axiom_flow`、`mode=api`、
    `provider=qwen`、`endpoint=vision`、`model`=vision_model）；engine=None 不写；
    DB 异常降级不抛（调用仍成功）；
  - `VisionClient(api_select="qed-engine")`：gateway 路径——POST
    `{gateway}/api/v1/llm/vision` `{pdf_base64, pdf_filename}` → `{reply, call_id, success,
    error}`；`success=false` 或 `reply` 非 str → 可读错误；超时/5xx/网络错误语义；
  - `is_gateway` 属性：`api_select == "qed-engine"`。
- [ ] **Step 2：运行确认失败**
- [ ] **Step 3：实现 llm_client.py**：
  - `VisionClient(api_select, api_key, model, gateway_url, timeout, max_attempts,
    transport, engine)`；
  - `recognize_page(image, page_no)`：local → 包裹 `QwenVLPlusClient`（复用其重试/错误语义）；
    qed-engine 且需 image 时 → gateway image_base64（预留）；
  - `parse_pdf(pdf_bytes, pdf_filename)`：qed-engine → gateway pdf_base64；
  - `_record_call(...)`：direct 成功后写 `qed_llm_calls`（列契约见根仓库
    `llm-gateway-and-model-management.md`），engine=None 或 DB 异常降级记日志；
- [ ] **Step 4：fallback/client.py 改造**：`QwenVLPlusClient.__init__` 缺省 `api_key` 改读
  `config.llm_api_key()`（`API_KEY`，不再读 `AXIOM_API_KEY`）；缺省 `model` 改读
  `Settings.vision_model`；`DEFAULT_MODEL` 常量更新为 `qwen-vl-plus`；
  `tests/unit/test_fallback.py` 相应适配（mock env 改 `API_KEY`）
- [ ] **Step 5：运行确认通过**
- [ ] **Step 6：提交**

#### Task 3：`scripts/axiom_flow_service.py --mode local|qed-engine`

**文件**：
- 修改：`scripts/axiom_flow_service.py`、`tests/unit/test_service_scripts.py`

- [ ] **Step 1：写失败测试**（扩展 `test_service_scripts.py`）：
  - `default_mode()`：自身 `.env` 的 `QED_API_SELECT`（缺省 `local`）；
  - `read_mode()`：`logs/qed-axiom-mode` 优先，缺省 `default_mode()`；
  - `start --mode qed-engine` 写 MODE_FILE 且 `_spawn` env 注入 `QED_API_SELECT=qed-engine`；
  - `start` 不传 `--mode` 时读 .env 值；
  - `status` 输出 `running (pid X, mode Y)`；
  - parser：`--mode choices=("local", "qed-engine")` 于 start/restart；
  - `_load_env` 注入键集扩展 `QED_*` + `API_KEY`（保留 `AXIOM_*`）。
- [ ] **Step 2：运行确认失败**
- [ ] **Step 3：实现**（参照 `QED-Tracker/scripts/qed_tracker_service.py`）：
  `MODE_FILE = LOG_DIR / "qed-axiom-mode"`；`default_mode/read_mode/write_mode`；
  `_spawn(mode)` env 注入；`cmd_start` 写模式；`cmd_status` 输出模式；parser 增
  `--mode`；`_load_env` 键集扩展
- [ ] **Step 4：运行确认通过**
- [ ] **Step 5：提交**

#### Task 4：`.env` 调整 + `model-mode-config.md` 定稿

**文件**：
- 修改：`.env`（gitignored）、`docs/design/model-mode-config.md`、
  `tests/contract/test_design_documents.py`

- [ ] **Step 1**：`.env` 调整为三项目约定（值本地生效，无 git 痕迹，回执注明）：
  ```ini
  QED_API_SELECT=local
  API_KEY=sk-<现值>            # 自 AXIOM_API_KEY 迁入
  QED_LLM_GATEWAY_URL=http://127.0.0.1:8900
  QED_DB_HOST=127.0.0.1
  QED_DB_PORT=3306
  QED_DB_NAME=qed
  QED_DB_USER=root
  QED_DB_PASSWORD=swfswf123
  AXIOM_VISION_MODEL=qwen-vl-plus   # 值由 qwen-vl-ocr 改为 qwen-vl-plus（对齐 QED_OCR_MODEL）
  ```
  删除 `AXIOM_API_KEY`、`AXIOM_VISION_CONTRACT_VERSION`、`AXIOM_VISION_MAX_TOKENS`、
  `AXIOM_VISION_PAGE_ATTEMPTS`、`AXIOM_MODEL_*`（按需保留 `AXIOM_DATA_DIR` 等路径变量）
- [ ] **Step 2**：`model-mode-config.md` 定稿 Accepted：模式命名 local/qed-engine（对齐
  QED-Tracker）、api_select 默认 local、API_KEY 唯一（AXIOM_API_KEY 退役）、
  `AXIOM_VISION_MODEL` ↔ 根仓库 `QED_OCR_MODEL`（qwen-vl-plus）、无兜底（V2-006 取消）、
  qed-engine 模式依赖根仓库 local 模式约束、MinerU 编排移交、`--mode` 契约、qed_llm_calls
  归属说明；`test_design_documents.py` CURRENT_DOCUMENTS += `model-mode-config.md`
- [ ] **Step 3**：契约测试：`pytest tests/contract/test_design_documents.py -q`
- [ ] **Step 4：提交**

### V2-005 重新做（阶段 2）

#### Task 5：分页 spike（MinerU 经网关）

**说明**：`mineru_parse`（根仓库）只返回全文 markdown；页级产物需切分策略。
- [ ] **Step 1**：确认根仓库 8900 `--mode local` + MinerU 容器在线（`infra-status`）
- [ ] **Step 2**：取 5 页小 PDF（Rudin 源书 1-5 页切片），经 8900 `/api/v1/llm/vision`
  `{pdf_base64}` 调用，保存 markdown 到 `data/spike/`（临时目录，不入 git）
- [ ] **Step 3**：检查 markdown 是否含分页标记（如 `<uml_page_break>`/`---`/页码样式）
  → 冻结策略：
  - 方案 A（有标记）：按标记切分；
  - 方案 B（无标记）：pymupdf 逐页切单页 PDF 分别上传（确定性分页）。
- [ ] **Step 4**：spike 结论写入本计划正文（更新冻结策略）并提交（仅文档/记录）

#### Task 6：`markdown_to_blocks` 支持 `source=mineru` + 页级落盘

**文件**：
- 修改：`src/axiom_flow/fallback/normalize.py`、`tests/unit/test_fallback.py`
- 创建：`src/axiom_flow/orchestrator/mineru_ingest.py`（或并入 llm_client 层，按 spike
  结论定）、`tests/unit/test_mineru_ingest.py`

- [ ] **Step 1：写失败测试**：
  - `markdown_to_blocks(md, page, source=Source.MINERU)` → `BlocksPage.source == "mineru"`
    （默认值兼容 qwen-vl-plus）；
  - 页级切分函数：输入全文 markdown（含分页标记，按 spike 结论）→ 输出
    `{page_no: page_markdown}`；每页独立归一化；
  - MinerU 内嵌图片引用（`![](images/...)`）在页级 markdown 中剥离（保留文字）；
  - 落盘函数：写 `pages/pXXXX.md` + `pages/pXXXX.blocks.json`（格式与现状一致）。
- [ ] **Step 2：运行确认失败**
- [ ] **Step 3：实现**
- [ ] **Step 4：运行确认通过**
- [ ] **Step 5：提交**

#### Task 7：parse-jobs 按服务模式路由 + 双模式冒烟

**文件**：
- 修改：`src/axiom_flow/api/main.py`、`tests/contract/test_api_v1_contract.py`

- [ ] **Step 1：写失败测试**（`test_api_v1_contract.py`）：
  - `parse-jobs` 在 api_select=local 时经 VisionClient 直连（mock transport）逐页产出
    md/blocks（source=qwen-vl-plus），job 结构不变；
  - api_select=qed-engine 时经网关 parse_pdf 整书解析 → 页级切分落盘（mock 网关）；
  - 错误语义：网关失败 → job failed 含原因。
- [ ] **Step 2：实现**：`api/main.py` 用 `config.load_settings()` + `VisionClient`（按
  服务模式路由）；`ParseJobCreate.strategy` 字段保留（契约兼容），hybrid 语义废弃
  （文档注明）；内存态同步执行保持现状
- [ ] **Step 3：运行确认通过**（契约 + 全量单测）
- [ ] **Step 4：提交**
- [ ] **Step 5：双模式真实冒烟**（需用户环境确认）：
  - local：`restart --wait`（默认 local）→ 提交 5 页 parse-jobs → qwen-vl 直连成功、
    产物落盘、qed_llm_calls 有 `service=axiom_flow` 记录；
  - qed-engine：`restart --wait --mode qed-engine`（根仓库 8900 保持 local）→ 提交
    5 页 parse-jobs → MinerU 经网关成功、页级产物落盘；
  - `status` 显示当前模式。

### 收口（阶段 3）

#### Task 8：MinerU 编排移交删除 + 文档同步（依赖 Task 7 冒烟通过）

**文件**：
- 删除：`scripts/compose.yaml`、`scripts/infra-up.ps1`/`infra-down.ps1`/
  `infra-status.ps1`/`infra-reset.ps1`、`scripts/docker/`、`scripts/smoke-api.sh`
- 修改：`docs/architecture/overview.md`（拓扑：MinerU 归 QED-Engine 网关）、
  `docs/architecture/code-map.md`（api/orchestrator 状态、fallback 职责、llm_client/config
  登记、scripts 表）、`docs/design/pdf-parsing-and-rendering.md`（MinerU 接入改经网关、
  启停脚本章节、拓扑 mermaid）、`docs/design/8902-integration-contract.md`（阶段④/⑤）、
  `docs/design/index.md`、`docs/guides/operations.md`（infra 章节移除/改指根仓库）、
  `docs/guides/development.md`、`README.md`、`AGENTS.md`（任务路由表/问题定位表）、
  `tests/contract/test_architecture_documents.py`（overview mermaid 标签更新）、
  `tests/contract/test_code_document_mapping.py`（如 DesignRef 变化）
- [ ] **Step 1**：冒烟通过后删除编排脚本（git rm）
- [ ] **Step 2**：文档同步（含 mermaid/元数据/链接）
- [ ] **Step 3**：契约测试：`pytest tests/contract -q`
- [ ] **Step 4：提交**

#### Task 9：收口

- [ ] **Step 1**：todo.md 行更新（V2-014/V2-005 Completed 证据、V2-006 Cancelled 已迁移、
  Plan 行状态同步）；completed.md 核对
- [ ] **Step 2**：code-map/设计最终同步；`test_tracker_governance.py` 等契约全绿
- [ ] **Step 3**：全量门禁：`pytest tests -q` + `ruff check src tests scripts`
- [ ] **Step 4**：回执文本（提交号 + 门禁/双模式冒烟证据 + 对齐 QED-Tracker 说明 +
  `AXIOM_VISION_MODEL` 值变更 + qed-engine 模式约束）交用户登记根仓库 REQ-044
- [ ] **Step 5：提交**

## 验证与验收

- 单元：`pytest tests/unit -q`（新增 test_config/test_llm_client/test_mineru_ingest +
  适配 test_fallback/test_service_scripts）。
- 契约：`pytest tests/contract -q`（tracker/plan/design/architecture/code-doc/api-v1）。
- 冒烟（人工 + 用户环境）：Task 7 Step 5 双模式真实调用。
- 门禁：`pytest tests -q` + `ruff check src tests scripts` 全绿。
- 采纳门槛：见「目标与成功标准」1-6 逐项核对。

## 回滚

- 代码各任务独立提交，单一提交可 revert；`llm_client.py`/`config.py` 新增文件删除即回退。
- `.env`（gitignored）改动不产生 git 痕迹：回滚时恢复原值即可（执行前先备份 `.env`）。
- MinerU 编排脚本删除前已完成真实冒烟（Task 7 证据先行）；如需回退可从 git 历史恢复
  （过渡期根仓库双份存在已可接受）。
- 冒烟产物仅落盘 `data/`（运行时目录，可整体删除）。

## 关闭与归档

- 任务逐项完成后：todo 行更新为 Completed，V2-014/V2-005 关闭入 completed.md；
  V2-006 已按 Cancelled 入台账。
- 本计划正文按文档规范选择性保留或归档；回执根仓库 REQ-044 后随根仓库轮次关闭。
- 未对齐项（qed_llm_calls 归属 service=qed_engine、qed-engine 模式依赖根仓库 local
  模式）记录于设计文档「待对齐」并在回执中声明。
