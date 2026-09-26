# V1.0 解析与知识库优化轮（v1-parsing-knowledge-optimization）

状态：Accepted
任务类型：B
最后更新：2026-09-26
关联 ADR：无新增（登记来源＝根仓库 ARCH-025 滚动壳「REQ-089 第一批调研承接」节的 DeepTutor 对照调研结论；治理沿用 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md) 一任务一承载）
关联设计：[parsing-pipeline.md](../design/parsing-pipeline.md)、[parsing-management.md](../design/parsing-management.md)、[database-design.md](../architecture/database-design.md)、[api.md](../architecture/api.md)
关联 Tracker：docs/trackers/todo.md（PLAN-009 镜像行；承载 ARCH-011、ARCH-021、ARCH-022/023）
归档判定：Retain（滚动登记与逐条实施里程碑证据，关闭时按当时里程碑归档）

> **登记沿革**：2026-09-22 用户裁决——根仓库「Axiom-Flow（V1.0）优化轮（ARCH-025）」的优化项
> 下沉本仓库登记，作为**本仓库后续 1.0 版本的任务**；本轮**只立项登记，不排实施**。
> 优化项清单与判定（吸收/改造/拒绝）源自根仓库对 `DeepTutor`（第三方开源项目，只读参照）
> 的三切片调研，证据路径已随条目抄录于此。

## 目标与成功标准

**目标（V1.0 形态）**：产物从「解析出来」升级为「大模型易消费 + 可检索」：

```
PDF → 解析管线（MinerU，已有）
  → blocks 语义分诊（版式块过滤 + 公式/表/图/代码保留 + 未知类型审计 ledger）
  → 知识单元（块级切分为主体：数学感知边界，公式不腰斩；块序号锚点）
  → 向量化（索引版本行增量：换模型写新版本、旧版可读可回滚）
  → 检索服务（8902 端点：向量 + BM25 混合 → RRF 融合 → 可选 rerank，
     命中返回书/页/块三级 self_ref 溯源）
```

**成功标准**：

1. P1~P3（解析面，ARCH-022）与 R1~R4（知识库面，ARCH-023）逐条实施，各带契约/单元测试；
2. 换模型/换参数不引发全量重解析（P1 内容寻址 + R1 版本行增量可证）；
3. 崩溃/半写不产生假就绪版本目录（P2 manifest-last 可证）；
4. 检索端点经根仓库探索验证界面（v0 切分校验 → v1 BM25 → v2 向量混合召回）联调验收，
   命中可回溯到页/块级原文。

## 范围与非目标

**范围**：解析产物质量与结构（重跑判定、就绪标记、语义分诊 ledger）+ 知识库面（切分、
向量化版本管理、混合检索配方、8902 检索端点契约）。

**非目标**：渲染与前端消费界面（根仓库 8903 前端的事，本仓库只保证契约与产物）；
**知识图谱暂不做**（2026-09-22 用户裁决，roadmap 预留，RAG 成熟后再裁）；多解析引擎
bridge（P4 暂缓）；查询改写（P 系外 R5 后置，问答面归根仓库学习中心）；纯文件无 DB
存储与 GraphRAG 拍平（R6 拒绝，判据见工作项表）。

## 前置条件

1. v0.1 主线解析管理闭环收口：PLAN-007 W6（b05 全本真机复测 + REQ-086/087 回执）与
   PLAN-008（页级失败清单与 jobs 端点）先完成——本壳条目的复测与验收现场复用其成果。
2. 实施排期经用户裁决（本轮仅登记；每条开工前按本仓库变更分级定级）。
3. 外部实验遵循 roadmap「冻结原则」：假设、样本、内容哈希、预算、采纳门槛先行。

## 工作项（V1.0 登记表；判定源自根仓库 DeepTutor 调研）

### ARCH-022 · 解析面优化（P 系列）

| # | 优化项 | 判定 | 来源/证据（DeepTutor 相对路径） | 状态 |
| --- | --- | --- | --- | --- |
| P1 | `parser_signature` 重跑判定：只哈希影响输出的解析参数（mode/模型版本/公式/表格/OCR 开关），`(源哈希, 签名)` 内容寻址判「要不要重跑」，登记进 af_* 表 | 吸收 | `services/parsing/cache.py`、`types.py:40` | 待排期 |
| P2 | manifest-last 就绪标记 + 半写目录失败重清（比 job 状态字段抗崩溃；与 PLAN-007 激活语义互补） | 吸收 | `services/parsing/cache.py`（reserve/cleanup_failed） | 待排期 |
| P3 | blocks 语义分诊策略：过滤页眉/页脚/页码，保留公式/表/图/代码，**未知类型宁收不丢 + 审计 ledger**，版本化策略表（v2 名称兼容） | 吸收（改造：落到 af_pages 块级标记） | `rag/pipelines/lightrag/block_policy.py` | 待排期 |
| P4 | 多解析引擎 bridge（Docling/markitdown/Tika… 统一 IR） | 暂缓 | 单 MinerU 主线，多引擎属远期 | 不排 |
| P5 | 双轨 IR（Markdown 保底 + blocks 富结构） | 已同构（维持） | `types.py:24` `ParsedDocument` | 无动作 |

### ARCH-023 · 知识库面（R 系列，8902 检索端点契约先行）

| # | 优化项 | 判定 | 来源/证据 | 状态 |
| --- | --- | --- | --- | --- |
| R1 | 索引版本行增量（EmbeddingSignature 语义：binding/模型/维度/base_url 哈希；变更写新版本、旧版可读可回滚；同签名只追加不重建）——MySQL 承载：af_* 或新表签名列 + 版本行 | 吸收（改造为 DB 承载） | `rag/index_versioning.py`、`llamaindex/pipeline.py:303-346` | 待排期 |
| R2 | 块级知识单元 + `self_ref="blocks.json#/{index}"` 页+块序号锚点（与 af_pages 同构，溯源必须保留） | 吸收 | `lightrag/sidecar.py:243,276-288` | 待排期 |
| R3 | 检索配方：vector 与 BM25 各取 2×top_k → RRF 融合 → 可选 CrossEncoder rerank（加载失败静默降级）；BM25 索引持久化 + top_k 按语料量 clamp | 吸收（轻量可抄） | `llamaindex/retrievers.py:138-150`、`rerank.py` | 待排期 |
| R4 | 数学感知切分：以 blocks 为切分单元，公式/定理块原子性（DeepTutor 无此答案，其主链路 SentenceSplitter 512/50 裸切；此为我方自研点） | 自研（根侧 REQ-082 草案承接） | 反例证据：`llamaindex/config.py` chunk_geometry | 待排期 |
| R5 | 查询改写：LLM ≤3 变体并行检索再聚合 | 后置 | `smart_retriever.py`；问答面归根仓库 | 不排 |
| R6 | 8 引擎 provider 抽象 / 纯文件无 DB 存储 / GraphRAG 拍平 txt 丢块结构 | 拒绝 | `rag/factory.py`、`graphrag/ingestion.py`（反例） | 无动作 |

## 承载 ARCH-011 · 产物重建批量窗口（D 类，待排期）

- **目的**：把 v0.1 侧未在业务库 `qed` 与真实数据根上执行的产物重建合并为一次批量窗口（其余
  书目全本重解析），使本壳 P1~P3/R1~R4 有可复测的真实现场——即「前置条件」所指的重建本身。
- **关闭条件**：与 todo `ARCH-011` 行同一判据（逐本 `active_job_id` 生效、`failed_pages` 为空、
  前后指纹留档）；窗口执行完毕即关闭该行，本壳的「产物重建」前置同时解除。
- **工作项**（窗口开工时按变更分级另拆 D 类计划，本节只登记不实施）：W0 待重解析清单
  （`GET /parse-jobs?status=partial&status=failed&status=interrupted` + `GET /books/{id}/pages`
  判据 `not has_artifact or status == "failed"`）；W1 备份（`mysqldump` af_* 四表 +
  `alembic_version`）与数据根前后指纹；W2 按需「先清后建」（`POST /api/v1/admin/purge`，双确认、
  作用域限 `parsed/`）；W3 逐本串行 ingest + 全本解析（**同一时刻只容一个 MinerU 占用**，排他
  判据＝`:5002/health` 的 queued+processing==0）；W4 逐本核对与只读复跑；W5 差异复核 + 六步收口。
- **状态**：`Blocked`（2026-09-26 自 todo「长期任务」节改判入「后续待做」节，类别长期 → 支线，
  本壳为承载）。开工前置＝窗口执行期先把业务库 `qed` 升 `20260926_0001`（承接原 PLAN-017 W7 真机
  验证——激活删旧与根视图新形状在批量重解析中顺带取证）。环境侧（容器/GPU/WSL 容量）归根仓库 8900 `scripts/image-model/`，本仓库只连
  不停、不据此调业务参数。

## 验证与验收

- 每条实施走本仓库六步与门禁（`pytest tests -q` + `ruff check src tests`）；P1/P2 补崩溃与
  重跑判定的契约/单元测试，R1~R4 补检索契约测试（含空白页与公式块样本）。
- 端点契约（检索/切分产出形状）先行成文进 `api.md`，根仓库确认后方可冻结（判例同 PLAN-008）。
- 终验：b05（含空白页书）全链路重跑——解析 → 分诊 → 切分 → 向量化 → 混合检索命中并回溯页/块。

## 回滚

登记壳自身无代码面：删除本文件不影响代码与测试。各条目按自身提交独立回滚；
R1 版本行增量设计本身要求旧版本索引可读可回滚。

## 关闭与归档

关闭条件：ARCH-022/023 全部条目关闭（实施或经用户裁决移出 1.0 范围）且 V1.0 终验达成；
关闭时本壳 Retain 归档 `history/plans/<year-month>/`，todo 移除 PLAN-009、ARCH-021/022/023 行。
