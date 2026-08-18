# 待做任务

状态：Current
最后更新：2026-08-18

本表是全部未关闭工作的统一导航（v2 纪元）。v1 任务台账已归档
（`docs/history/v1-20260814/trackers/todo.md`）。Plan 行镜像计划正文状态；其他行在具备
范围、前置条件、验证与成功标准后建立独立计划，并保留原 ID。

## v2 任务清单

| ID | 类型 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| DOCS-001 | Plan | P0 | Accepted | [计划 2026-08：v2 docs 重构轮](../plans/2026-08-v2-docs-rebuild.md) | 计划正文 Accepted；P1-P9 已完成，契约测试 42 全绿 + ruff 通过，用户确认后关闭归档 |
| V2-001 | Infra | P0 | Completed | WSL 容器基建：Docker Compose（mineru-api 单体，内嵌 vLLM）+ 启停脚本（infra-up/down/status/reset） | `scripts/compose.yaml` 与 4 个 ps1 就位；容器 healthy，Windows→8002 连通，sample.pdf 端到端解析成功（hybrid-engine，3.4.4）；router 模式 502 不可用已记录，改单体方案 |
| V2-002 | Feature | P0 | Completed | 新包结构与 schemas：`src/axiom_flow/{api,orchestrator,ingest,fallback}/` + `schemas.py`（blocks/page/book/job Pydantic 模型） | `tests/unit/test_schemas.py` 31 通过；契约 42 + ruff 全绿；code-map 同步（v1 旧代码与测试已按 spec 清除） |
| V2-003 | Feature | P0 | Completed | ingest：PDF 导入、页图渲染（150 DPI+）、book.json（SHA-256） | 已审阅 2026-08-16 QED-Engine 根仓库侧越界产物（`src/axiom_flow/ingest/importer.py` + `tests/unit/test_ingest.py` 8 测试）：与设计一致，全量 81 passed + ruff clean，已按本仓库流程提交（commit 见 git 历史）；设计文档/code-map 同步 |
| V2-004 | Feature | P0 | Open | orchestrator：任务编排 + state.sqlite（jobs/pages）+ 质量信号（空块率/公式置信度/表格成功数） | **根仓库 C 组联调前置请求（2026-08-16）**：8902 API 服务建立（V2-003~007）后回执根仓库（integration-matrix C 组 / REQ-034/036 衔接）；单元 + 集成（mock MinerU）测试通过 |
| V2-005 | Feature | P0 | Open | MinerU 接入：HTTP 上传 PDF → 解析 → 结果落盘（md/blocks/页图） | 同 V2-004 联调前置请求；冒烟测试：单 PDF 5 页含公式跑通 |
| V2-006 | Feature | P1 | Open | fallback：qwen-vl-plus 兜底通道（strategy=hybrid，source 标记） | 构造低质量页验证兜底触发 |
| V2-007 | Feature | P1 | In Progress | API v1 端点：parse-jobs/books/pages/manifest | **第一版已实现并启动（2026-08-16，`ca06a11`）**：8902 服务在线（books/pages/image/manifest/parse-jobs），契约测试 11 通过，冒烟全通；契约草案见 [8902 集成契约与联调对齐](../design/8902-integration-contract.md)，**冻结节点待根仓库 REQ-034 评审确认** |
| V2-008 | Gap | P1 | Open | 2 本代表书验收（Rudin 英文 + 陈纪修中文） | 抽查 20 页/本公式可渲染 ≥90%；验收标准逐项通过 |
| V2-009 | Gap | P2 | Open | 批量处理其余 10 本 | V2-008 关闭后拆 D 类计划执行 |
| V2-010 | Plan | P0 | Accepted | [计划 2026-08：qwen-vl-plus 最小闭环实验](../plans/2026-08-qwen-vl-plus-trial.md) | 计划正文 Accepted；**实验已执行（2026-08-16）**：20 页成功 0 失败，公式可解析率 100%（4/4 + 365/365 行内），text_length_ratio 全 ≥0.7，预算 <1 元（35890 tokens）；采纳门槛 ①③ 已达标，② 人工抽查待用户；通过后 ADR 接受先导引擎并调主线顺序 |
| V2-011 | Gap | P1 | In Progress | 生命周期脚本 `scripts/axiom_flow_service.py`（服务化，对齐根仓库 REQ-017①/REQ-039） | **已提交（`f5d9355`，2026-08-18）**：[服务生命周期脚本设计](../design/service-lifecycle.md)（Accepted/Implemented）+ `scripts/axiom_flow_service.py` + `tests/unit/test_service_scripts.py` + `src/axiom_flow/api/main.py` 加 `/api/v1/health`；**根仓库侧已接入**（8900 axiom 单元黑盒调用本脚本，stop/restart 冒烟通过，REQ-039 回执前提示）；**回执根仓库 REQ-039 待用户确认后执行** |
| V2-012 | Defect | P1 | Completed | 生命周期脚本 `_pid_is_alive` 编码修复（需求方：QED-Engine REQ-040，设计：docs/design/service-lifecycle-encoding-fix.md）：中文 Windows tasklist 输出 GBK 表头 → subprocess `text=True` utf-8 解码失败 → readerthread 中断 → stdout=None → `str(pid) in result.stdout` 抛 TypeError → 停止/重启脚本退出码 1（控制台报「停止失败」） | **已修复并提交（`729d208`）**：`errors="replace"` + `(result.stdout or "")` 同构修复 + 4 条回归测试（stdout=None 兜底/errors 传参/ASCII 匹配/不含 PID）；全量门禁 pytest tests -q 137 passed + `ruff check src tests scripts` 全绿（含修复 cmd_status 未使用变量 `port`）；真实 8902 start/status/restart --wait/stop/status 全链路 exit 0；回执根仓库 REQ-040（提交号 `729d208` + 门禁/冒烟输出见上） |
| V2-013 | Feature | P1 | Open | af_* 书目同步与块判定（需求方：QED-Engine 文档解析管理轮，设计：[af-books-sync](../design/af-books-sync.md)）：af_books / af_block_reviews 建表（Alembic）+ `POST /books/sync`（幂等 upsert）+ `/books` 改读 af_books（含课程/进度字段，空表回退文件系统）+ 块判定端点（PUT/GET review）+ parse-jobs 完成后回写 pages_done/parse_status | **根仓库 C 组联调前置请求（2026-08-18 登记）**：契约草案见设计文档；8900 侧（REQ-041）与前端同步开发，契约冻结后微调；用户评审确认后执行，回执根仓库 REQ-041 |

## 探索路线图

Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验/重渲染）、学习交互闭环
——见 [路线图](roadmap.md)，按需立项。
