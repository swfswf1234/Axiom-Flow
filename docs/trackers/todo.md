# 待做任务

状态：Current
最后更新：2026-08-14

本表是全部未关闭工作的统一导航（v2 纪元）。v1 任务台账已归档
（`docs/history/v1-20260814/trackers/todo.md`）。Plan 行镜像计划正文状态；其他行在具备
范围、前置条件、验证与成功标准后建立独立计划，并保留原 ID。

## v2 任务清单

| ID | 类型 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| DOCS-001 | Plan | P0 | Accepted | [计划 2026-08：v2 docs 重构轮](../plans/2026-08-v2-docs-rebuild.md) | 计划正文 Accepted；P1-P9 已完成，契约测试 42 全绿 + ruff 通过，用户确认后关闭归档 |
| V2-001 | Infra | P0 | Completed | WSL 容器基建：Docker Compose（mineru-api 单体，内嵌 vLLM）+ 启停脚本（infra-up/down/status/reset） | `scripts/compose.yaml` 与 4 个 ps1 就位；容器 healthy，Windows→8002 连通，sample.pdf 端到端解析成功（hybrid-engine，3.4.4）；router 模式 502 不可用已记录，改单体方案 |
| V2-002 | Feature | P0 | Completed | 新包结构与 schemas：`src/axiom_flow/{api,orchestrator,ingest,fallback}/` + `schemas.py`（blocks/page/book/job Pydantic 模型） | `tests/unit/test_schemas.py` 31 通过；契约 42 + ruff 全绿；code-map 同步（v1 旧代码与测试已按 spec 清除） |
| V2-003 | Feature | P0 | Open | ingest：PDF 导入、页图渲染（150 DPI+）、book.json（SHA-256） | 单元测试通过；产物目录结构符合设计 |
| V2-004 | Feature | P0 | Open | orchestrator：任务编排 + state.sqlite（jobs/pages）+ 质量信号（空块率/公式置信度/表格成功数） | 单元 + 集成（mock MinerU）测试通过 |
| V2-005 | Feature | P0 | Open | MinerU 接入：HTTP 上传 PDF → 解析 → 结果落盘（md/blocks/页图） | 冒烟测试：单 PDF 5 页含公式跑通 |
| V2-006 | Feature | P1 | Open | fallback：qwen-vl-plus 兜底通道（strategy=hybrid，source 标记） | 构造低质量页验证兜底触发 |
| V2-007 | Feature | P1 | Open | API v1 端点：parse-jobs/books/pages/manifest | 契约测试通过；返回结构与 schemas 一致 |
| V2-008 | Gap | P1 | Open | 2 本代表书验收（Rudin 英文 + 陈纪修中文） | 抽查 20 页/本公式可渲染 ≥90%；验收标准逐项通过 |
| V2-009 | Gap | P2 | Open | 批量处理其余 10 本 | V2-008 关闭后拆 D 类计划执行 |

## 探索路线图

Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验/重渲染）、学习交互闭环
——见 [路线图](roadmap.md)，按需立项。
