# ADR 0004：解析管线归位与模型直连（对齐根仓库 ADR 0014）

状态：Accepted
最后更新：2026-09-17
关联代码：`src/axiom_flow/engines/`（待实现，规划）、`src/axiom_flow/orchestrator/`、`src/axiom_flow/ingest/`
关联测试：`tests/contract/test_architecture_documents.py`、`tests/contract/test_design_documents.py`

## 背景

v0.1 模型形态（[ADR 0002](0002-pending-design-location.md) 承接的
[model-mode-config](../history/design/model-mode-config.md)）：Axiom-Flow 的 `llm_client.py`
双模式——`local` 直连 qwen-vl、`qed-engine` 经 8900 网关（MinerU 仅经网关可达）。该形态在
「文档解析管理」联调中暴露三处不匹配：

1. 8900 `/llm/vision` 对 MinerU 只回传 markdown 字符串，丢失块级坐标（bbox），无法支撑对照与编辑；
2. 模型调用/预处理/后处理跨两仓库，解析效果调试无法一端闭环；
3. 模型选择写进 Axiom-Flow 业务调用链，换模型（MinerU / PaddleOCR-VL）需改动业务代码。

根仓库同轮立 [ADR 0014](https://github.com/QED-Engine/qed-engine/blob/main/docs/adr/0014-parsing-ownership-and-model-boundary.md)
（2026-09-14，Accepted）：**模型生命周期归 8900，解析管线归 Axiom-Flow**。

## 决定

1. **Axiom-Flow 是解析能力的唯一实现与调试位置**：PDF ingest、引擎适配、后处理归一化、
   解析编排、产物落盘、对照供给、人工编辑落库全部在本仓库实现。
2. **解析直连模型服务**：Axiom-Flow 经引擎适配器直连本地模型服务（MinerU 8002 等），
   **不再经 8900 网关**；模型服务地址可配置（部署形态需要时可指向 8900 代理，契约不变）。
   `llm_client.py` 双模式视觉调用退役，由 `engines/` 适配器承接。
   （勘误 2026-09-17：MinerU 端口 8002 → 5002，直连地址键 `QED_MINERU_URL` →
   `QED_OCR_MODEL_URL`，不保留旧键别名；不影响本决定语义。）
3. **模型无感知**：引擎由配置选择（`AXIOM_OCR_ENGINE=mineru|paddleocr-vl|qwen-vl`，默认
   `mineru`）；统一 blocks schema 为内部事实源，模型差异在适配层归一；换模型只改配置与适配器。
4. **模型生命周期不归本仓库**：模型文件槽位、启停、探针与资源互斥由根仓库 8900 管理；
   本仓库只消费模型服务端点。**模型调参**（引擎参数等）同归 8900，本仓库不暴露调参入口
   （2026-09-17 用户裁决，PLAN-002）。
5. **数据落点**：产物落 `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/`；数据库
   统一 `qed` 库 `af_*` 命名空间四表（af_books/af_parse_jobs/af_pages/af_block_edits）。
6. **独立性**：解析不再要求 8900 在线；模型服务可经根仓库脚本手动启停。`qed_llm_calls`
   调用记录由本仓库在直连成功路径自写（`service=axiom_flow`）。

## 后果

- 解析调试与实现单仓库闭环；新增引擎或换模型只动适配层与配置。
- 原 `llm_client.py`、`fallback/` 通道与 `model-mode-config.md` 退役（设计归档 `history/design/`）；
  `af-books-sync.md` 的 af_* 两表草案被四表设计取代（归档）。
- 8900 侧保留模型运维面；前端经 8900 适配访问 8902（根仓库 ADR 0007 不变）。
- 本仓库文档体系（AGENTS/architecture/design/trackers/guides）按新形态重构（2026-09-14 轮）。

## 取代

取代：无（部分修订 [ADR 0002](0002-pending-design-location.md) 承接的 `model-mode-config.md`
中「MinerU 仅经 8900 网关可达」的解析路径，该设计文档归档 `history/design/`）。
被取代：无。
来源：根仓库 [ADR 0014](https://github.com/QED-Engine/qed-engine/blob/main/docs/adr/0014-parsing-ownership-and-model-boundary.md)（同轮对齐）。
