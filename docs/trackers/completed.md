# 关闭台账

状态：Current
最后更新：2026-08-20

本表登记已关闭任务（v2 纪元）。v1 关闭台账已归档
（`docs/history/v1-20260814/trackers/completed.md`）。

| 日期 | ID | 任务 | 终态 | 关闭结果 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 2026-08-20 | V2-001 | WSL 容器基建：Docker Compose（mineru-api 单体，内嵌 vLLM）+ 启停脚本（infra-up/down/status/reset） | Completed | Achieved | `scripts/compose.yaml` + 4 个 ps1 就位；容器 healthy，Windows→8002 连通，sample.pdf 端到端解析成功（hybrid-engine 3.4.4）；router 模式 502 弃用改单体；**MinerU 编排已于 V2-014 移交根仓库 `scripts/image-model/`** |
| 2026-08-20 | V2-002 | 新包结构与 schemas：`src/axiom_flow/{api,orchestrator,ingest,fallback}/` + `schemas.py`（blocks/page/book/job Pydantic 模型） | Completed | Achieved | `tests/unit/test_schemas.py` 31 通过；契约 42 + ruff 全绿；code-map 同步（v1 旧代码与测试已按 spec 清除） |
| 2026-08-20 | V2-003 | ingest：PDF 导入、页图渲染（150 DPI+）、book.json（SHA-256） | Completed | Achieved | 已审阅 2026-08-16 根仓库侧越界产物（`importer.py` + 8 测试）与设计一致；全量 81 passed + ruff clean；设计文档/code-map 同步 |
| 2026-08-20 | V2-006 | fallback：qwen-vl-plus 兜底通道（strategy=hybrid，source 标记） | Cancelled | Not Applicable | 2026-08-20 用户裁决：不用兜底，服务只用一个图像模型（local 直连 qwen-vl / qed-engine 经网关 MinerU 双模式） |
| 2026-08-20 | V2-010 | qwen-vl-plus 最小闭环实验（C 类计划 2026-08-qwen-vl-plus-trial） | Completed | Achieved | 20 页成功 0 失败，公式可解析率 100%（4/4 + 365/365 行内），text_length_ratio 全 ≥0.7，预算 <1 元；实验证据随计划归档 `docs/history/plans/2026-08/`；qwen-vl-plus 作为 local 模式默认通道被采纳（V2-014） |
| 2026-08-20 | V2-011 | 生命周期脚本 `scripts/axiom_flow_service.py`（服务化，对齐根仓库 REQ-017①/REQ-039） | Completed | Achieved | 提交 `f5d9355`：service-lifecycle.md（Accepted/Implemented）+ test_service_scripts.py + `/api/v1/health`；根仓库 8900 已接入（stop/restart 冒烟通过）；回执 REQ-039（提交号 `f5d9355` + 门禁输出）已交用户登记 |
| 2026-08-20 | V2-012 | 生命周期脚本 `_pid_is_alive` 编码修复（需求方：REQ-040） | Completed | Achieved | 修复 `729d208`：`errors="replace"` + stdout 空值兜底 + 4 回归测试；全量 137 passed + ruff 全绿；8902 start/status/restart/stop 全链路 exit 0；回执 REQ-040（提交号 `729d208`） |
| 2026-08-20 | DOCS-001 | v2 docs 重构轮（计划 2026-08-v2-docs-rebuild） | Completed | Achieved | P1-P9 已完成、契约测试 42 全绿 + ruff 通过；计划正文完成事实已同步至 git 历史，按文档规范删除（普通关闭计划） |
| 2026-08-21 | MODEL-001 | 模型模式与 MinerU 网关轮（计划 2026-08-model-mode-and-mineru-gateway.md） | Completed | Achieved | T1-T7 全部通过：config.py(19 tests)/llm_client.py(16 tests + fallback 重构)/service --mode(35 tests)/文档同步+契约修复；T5 本地冒烟 qwen-vl 直连通过；T6 网关冒烟 8900 /llm/vision 通过（call_id=17）；T7 跨项目 8900→8902 健康检查通过；提交 `fcb9eb9..c212e8e`（9 commits）；门禁 pytest 179 passed + ruff 全绿；V2-015 todo 行类型问题为外部并发变更，非本任务范围 |
