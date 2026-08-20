# 待做任务

状态：Current
最后更新：2026-08-20

本表是全部未关闭工作的统一导航（v2 纪元）。v1 任务台账已归档
（`docs/history/v1-20260814/trackers/todo.md`）。Plan 行镜像计划正文状态；其他行在具备
范围、前置条件、验证与成功标准后建立独立计划，并保留原 ID。

## v2 任务清单

| ID | 类型 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| MODEL-001 | Plan | P0 | Accepted | [计划 2026-08：模型模式与 MinerU 网关轮](../plans/2026-08-model-mode-and-mineru-gateway.md) | 计划正文 Accepted；执行 V2-014（config.py/llm_client.py/--mode/MinerU 移交）与 V2-005（MinerU 经网关最小闭环）；V2-006 已取消（2026-08-20 用户裁决：不用兜底） |
| V2-004 | Feature | P0 | Open | orchestrator：任务编排 + state.sqlite（jobs/pages）+ 质量信号（空块率/公式置信度/表格成功数） | **根仓库 C 组联调前置请求（2026-08-16）**：8902 API 服务建立（V2-003~007）后回执根仓库（integration-matrix C 组 / REQ-034/036 衔接）；单元 + 集成（mock MinerU）测试通过 |
| V2-005 | Feature | P0 | In Progress | MinerU 接入：HTTP 上传 PDF → 解析 → 结果落盘（md/blocks/页图） | **V2-014 轮重新做（MODEL-001）**：MinerU 编排移交根仓库 `scripts/image-model/`；qed-engine 模式经 8900 网关 `/llm/vision`（pdf_base64 → 全文 markdown → 页级切分落盘）；local 模式 qwen-vl 直连保持；双模式冒烟验证（计划 Task 5-7） |
| V2-007 | Feature | P1 | In Progress | API v1 端点：parse-jobs/books/pages/manifest | **第一版已实现并启动（2026-08-16，`ca06a11`）**：8902 服务在线（books/pages/image/manifest/parse-jobs），契约测试 11 通过，冒烟全通；契约草案见 [8902 集成契约与联调对齐](../design/8902-integration-contract.md)，**冻结节点待根仓库 REQ-034 评审确认** |
| V2-008 | Gap | P1 | Open | 2 本代表书验收（Rudin 英文 + 陈纪修中文） | 抽查 20 页/本公式可渲染 ≥90%；验收标准逐项通过 |
| V2-009 | Gap | P2 | Open | 批量处理其余 10 本 | V2-008 关闭后拆 D 类计划执行 |
| V2-013 | Feature | P1 | Open | af_* 书目同步与块判定（需求方：QED-Engine 文档解析管理轮，设计：[af-books-sync](../design/af-books-sync.md)）：af_books / af_block_reviews 建表（Alembic）+ `POST /books/sync`（幂等 upsert）+ `/books` 改读 af_books（含课程/进度字段，空表回退文件系统）+ 块判定端点（PUT/GET review）+ parse-jobs 完成后回写 pages_done/parse_status | **根仓库 C 组联调前置请求（2026-08-18 登记）**：契约草案见设计文档；8900 侧（REQ-041）与前端同步开发，契约冻结后微调；用户评审确认后执行，回执根仓库 REQ-041 |
| V2-014 | Feature | P1 | In Progress | 模型模式与 MinerU 移交（REQ-044）（需求方：QED-Engine 根仓库 LLM 网关与模型管理轮，设计：[model-mode-config](../design/model-mode-config.md)）：① `.env` 调整（`QED_API_SELECT=local` 默认、`API_KEY` 唯一、`QED_LLM_GATEWAY_URL`、`QED_DB_*`）；② `src/axiom_flow/config.py` + `llm_client.py` 双模式（local 直连 qwen-vl / qed-engine 经 8900 `/llm/vision` 调 MinerU，**MinerU 仅经网关可达**）；③ `scripts/axiom_flow_service.py --mode local/qed-engine`（重启可换模式）；④ **MinerU 编排移交**：compose.yaml/infra-*.ps1/docker/Dockerfile 迁出至根仓库 `scripts/image-model/`，本地 mineru 直连调用删除；⑤ local 模式直连调用记录写 `qed_llm_calls`（`service=axiom_flow`） | **MODEL-001 轮执行中（2026-08-20）**：设计定稿 Accepted；实现对齐 QED-Tracker（REQ-043 先例：local/qed-engine 双模式、config.py、`--mode`）；MinerU 编排移除、双模式冒烟后回执根仓库 REQ-044（含命名/约束说明） |

## 探索路线图

Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验/重渲染）、学习交互闭环
——见 [路线图](roadmap.md)，按需立项。
