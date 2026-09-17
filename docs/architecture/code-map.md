# 代码映射（v2）

状态：Current
确认状态：暂定
最后更新：2026-09-17

本表登记受管模块与其设计文档（DesignRef）、测试的对应关系。v1 模块已归档
（`docs/history/v1-20260814/`），不在此列。**ARCH-020 重构（2026-09-14）**后目标包结构：
api / ingest / engines / normalize / orchestrator / db / schemas / config。

## 受管模块

| 路径 | 职责 | 状态 | 设计 | 测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `src/axiom_flow/api/` | 对外 `/api/v1` 路由，只做协议适配 | **Current** | `docs/architecture/api.md` | 契约测试 | 现有端点读文件系统；ARCH-002~007 扩展 |
| `src/axiom_flow/ingest/` | PDF 导入、页图渲染、book.json（SHA-256） | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_ingest.py` | 产物落点迁 `QED_DATA_ROOT/parsed`（ARCH-015） |
| `src/axiom_flow/orchestrator/` | 解析任务编排、页状态、质量信号、重试 | Pending | `docs/design/parsing-pipeline.md` | 集成测试 | ARCH-003；落 af_parse_jobs/af_pages |
| `src/axiom_flow/engines/` | 引擎适配器（mineru / paddleocr-vl / qwen-vl） | Pending | `docs/design/model-integration.md` | 单元测试（mock） | ARCH-016；统一 blocks 归一化入口 |
| `src/axiom_flow/normalize/` | 模型输出 → 统一 blocks schema 归一化 | Pending | `docs/design/parsing-pipeline.md` | 单元测试 | ARCH-016 |
| `src/axiom_flow/db/` | af_* 四表 ORM/仓储（Alembic 迁移） | Pending | `docs/design/parsing-management.md` | `tests/unit/` | ARCH-002/003 |
| `src/axiom_flow/schemas.py` | 统一格式 Pydantic 模型（blocks/page/book/job/edit） | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_schemas.py` | 内外契约单一事实源 |
| `src/axiom_flow/config.py` | 统一配置（`.env` 向上走查、数据根、引擎、端口） | **Current** | `docs/design/model-integration.md` | `tests/unit/test_config.py` | `AXIOM_OCR_ENGINE` / `QED_OCR_MODEL_URL`（5002）/ `QED_DATA_ROOT`；v1 键退役 |
| `src/axiom_flow/fallback/` | qwen-vl-plus 客户端（历史直连通道） | Deprecated | `docs/design/model-integration.md` | `tests/unit/test_fallback.py` | ARCH-016 由 `engines/qwen_vl` 取代后退役 |
| `src/axiom_flow/llm_client.py` | VisionClient（历史双模式视觉调用） | Deprecated | `docs/design/model-integration.md` | `tests/unit/test_llm_client.py` | ARCH-016 退役（解析直连，[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)） |

## 非受管配置

| 路径 | 职责 |
| --- | --- |
| `scripts/axiom_flow_service.py` | 8902 API 服务唯一启停脚本（start/stop/restart/status，DesignRef：`docs/architecture/api.md` ①生命周期与健康） |
| `scripts/tmp/` | 临时/试验脚本（试验运行器、质量统计、旧冒烟脚本等） |
| `data/` | 过渡期工作目录（ignore、可再生）；ARCH-015 后产物落 `QED_DATA_ROOT/parsed/` |
| `evaluation/` | 评估专区（样本与报告，与主链路隔离） |
| `web/` | 已移交 QED-Engine，本仓库不再维护 |
| `model/`（根仓库） | 模型文件槽位（由根仓库 8900 管理，本仓库只读引用） |
