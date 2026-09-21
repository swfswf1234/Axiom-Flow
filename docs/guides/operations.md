# 运维指南

状态：Current
最后更新：2026-09-21

## 8902 API 服务生命周期

本仓库 8902 API 服务（`uvicorn axiom_flow.api.main:app`，Windows 侧）启停由生命周期脚本
`scripts/axiom_flow_service.py` 管理（契约见 [8902 API 接口文档](../architecture/api.md) ①生命周期与健康）：

```powershell
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py start --wait
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py status
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py restart --wait
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py stop
```

- 就绪判据：`GET /api/v1/health` 返回 200（8900 控制中心与 `start-all.ps1` 探测路径一致）。
- PID 文件 `logs/qed-axiom.pid`，子进程日志 `logs/qed-axiom-serve.log`。
- 端口来源：`QED_AXIOM_URL` 解析 > `AXIOM_PORT` > 8902。
- 配置语境：脚本不再传 `--mode`（2026-09-17 配置简化，PLAN-002）——解析始终直连模型服务
  （[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)），配置由 `config.py`
  从仓库根向上走查 `.env` 自读；变量清单见[开发指南](development.md)。

### 停止可靠性语义（2026-09-04，与根仓库 8900/8903 脚本同构）

- 判活用内核句柄探测（`_proc_alive`，探测失败不再误判为「已死」）；优雅信号（CTRL_BREAK）
  未生效由 taskkill 强杀兜底成功时回显 `stopped (forced)`——正常现象，conda run 等跨
  console 语境常见，无需处理。
- 优雅+强杀后仍存活则退出码 1 显式报 `stop failed`（绝不假 stopped）：按提示手动
  `taskkill /PID <pid> /T /F`，确认进程退出后重试 `stop` 清理 PID 文件。
- 退出码：`0` 成功/幂等、`1` 运行失败（含 stop 无法终止）、`2` 参数错误。

## 推理基础设施（根仓库托管）

`mineru-api`（MinerU FastAPI 单体，内嵌 vLLM，hybrid-engine，GPU 穿透）与容器启停脚本
（`compose.yaml`、`infra-up/down/status/reset.ps1`、`Dockerfile`）已随 V2-014 移交 QED-Engine
根仓库 `scripts/image-model/`（2026-08-20）；本仓库不再维护容器脚本，只读引用其文档。

- 调用路径：Axiom-Flow 经引擎适配器**直连模型服务**（MinerU `QED_OCR_MODEL_URL`，默认
  `http://127.0.0.1:5002`，健康端点 `/health`），不经 8900 网关；引擎与地址配置见
  [model-integration](../design/model-integration.md)。模型生命周期（启停/探针/资源互斥）由
  8900 `/models/vision` 托管，本仓库只连接、不启停。
- 容器启停、健康检查、GPU 穿透与 WSL keepalive 排障以根仓库 `scripts/image-model/` 为准，
  本仓库不复制其正文。

## 数据目录

| 路径 | 内容 |
| --- | --- |
| `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/` | **根视图**：`book.json`、`pages/p<NNNN>.png`（150 DPI 页图正本，ingest 产物）、`manifest.json`；重新 ingest 只原子替换这三者 |
| `…/<book_id>/versions/<job_id>/` | **一次解析任务 = 一个版本**（PLAN-004 / REQ-080）：`book.json`、`pages/p<NNNN>.{png,md,blocks.json}`、`chunks.json`、`manifest.json`；生效版本由 `af_books.active_job_id` 指认，页图为根视图页图的**硬链接**（同一页图在 N 个版本不重复占盘） |
| `<QED_DATA_ROOT>/raw/<domain_id>/<course_id>/*.pdf` | 源书库（只读，QED-Tracker 写入；如 `math-advanced/math_analysis/`） |
| `<QED_DATA_ROOT>/tmp/` | **本服务不写**：引擎中间产物只在内存（PLAN-003 条文 12 / PLAN-004 V10），`tmp/axiom-flow/<job_id>/` 一旦存在即缺陷 |
| `data/` | 仓库内探针与合成样本留档区（`data/probes/`，`.gitignore` 内不入版本库）；**不作数据根候选**，真实数据根唯一事实源是 `QED_DATA_ROOT` |

产物只落 Windows 本地；WSL 不读写 Windows 文件系统（MinerU 容器不挂载 Windows 盘）。

老版本布局（扁平产物）的兼容：升级与迁移**不搬移**旧产物、不改 `af_pages` 行内容，端点读到
`versions/<job_id>/` 缺失时按 `af_books.active_job_id IS NULL` 走**根视图**（旧布局的
`pages/p<NNNN>.{md,blocks.json}` 与 `chunks.json` 直接可读），因此重解析前的窗口内 8900/8903
功能不中断。

## af_* 迁移单窗口 runbook（PLAN-004 / REQ-080，**D 类：执行前须用户批准**）

`20260920_0002` 改四表结构（生效指针 + `job_id` 作用域），且**必须与新代码同窗口发布**——ORM 声明了
`active_job_id` 而库未迁移时读列即故障。顺序固定为 B（本地全绿）→ C（单窗口）→ D（版本布局上单次
重解析），**不在旧布局上重复真机解析**（避免解析完即被回填成 `legacy` 而失效）：

```powershell
# C-1 停服（避免运行中写库）
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py stop
# C-2 备份 af_* 四表（含 alembic_version），例：导出到带日期的 .sql 后核对行数
& mysqldump --single-transaction qed af_books af_parse_jobs af_pages af_block_edits alembic_version > af_backup_YYYYMMDD.sql
# C-3 迁移（仓库根目录，读 .env 的 QED_DB_*）
& D:\software\anaconda3\envs\QED_env\python.exe -m alembic upgrade 20260920_0002
# C-4 校验：版本行 + 存量作用域 + 生效指针恒 NULL（存量不自动改语义）
#   alembic_version = 20260920_0002；SELECT DISTINCT job_id FROM af_pages;（预期 legacy 或单书唯一 job）
#   SELECT book_id, active_job_id FROM af_books;（预期全 NULL）
# C-5 清理 af_pages 幽灵行（第 999 页等无产物行）——**单独授权后才删**
# C-6 起服并只读冒烟
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py start --wait
```

- **回滚**：`python -m alembic downgrade 20260917_0001` 是**有损**操作（旧结构无法表达多版本，按
  `(书, 页)`/`(书, 页, 块)` 只保留 `job_id` 最小的一行），因此回滚优先用 C-2 的备份还原；产物文件
  不随迁移删除，DB 与文件解耦。
- **D 步（重解析）**：在版本布局上对目标书提交一次解析任务，产物落 `versions/<job_id>/`，成功后
  `active_job_id` 指向该版本；重解析同时作为 PLAN-003 W8 的续跑证据。
- 迁移期间 8902 离线：8900 返回 503 并降级显示（独立性铁律），不影响配置域与其他界面。

## 故障排查

| 现象 | 处置 |
| --- | --- |
| `stop` 回显 `stopped (forced)` | 优雅信号（CTRL_BREAK）未生效（conda run 等跨 console 语境常见），taskkill 强杀兜底成功——正常现象，无需处理 |
| `stop` 回显 `stop failed: pid …` | 按提示手动 `taskkill /PID <pid> /T /F`，确认进程退出后重试 `stop` 清理 PID 文件 |
| API 返回 503（模型服务不可用） | 确认 MinerU 服务在线：经 8900 `/models/vision/start`（旧名 `/models/mineru/start`）或根仓库 `scripts/image-model/` 脚本；再确认 `QED_OCR_MODEL_URL`（5002）可达 |
| 只读端点 404 `生效版本产物缺失：<book_id> @ <job_id>` | `af_books.active_job_id` 指向的版本目录被外部删除/未落盘（不回落根视图，ADR 0004）。核对 `versions/` 是否在盘、`GET /books/{id}/versions` 各行 `parsed`；确认产物不可恢复后重新解析或把指针置 NULL（`UPDATE af_books SET active_job_id=NULL WHERE book_id=…`，置 NULL 属数据修正，按 D 类先备份） |
| 容器已起但解析慢/超时 | 由根仓库 `scripts/image-model/` 排查 GPU 穿透与 vLLM warmup（首次约 1 分钟）；整本解析超时调大 `AXIOM_OCR_TIMEOUT`（默认 600s） |
| 模型异常 | 由根仓库 `scripts/image-model/` 重建容器/镜像 |
| WSL 不可达 | `wsl -l -v` 确认状态；容器由根仓库 `scripts/image-model/` 脚本恢复 |

## 数据操作分类

日常小规模数据操作（单书导入、重跑单页）为 A 类，git 提交留痕即可；大规模批量（如全部
12 本重解析）或受保护环境操作需按任务生命周期建立 D 类计划（备份 + 回滚 + 差异复核）。
