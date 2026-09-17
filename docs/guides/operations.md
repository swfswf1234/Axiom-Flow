# 运维指南

状态：Current
最后更新：2026-09-17

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
| `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/` | 每本书解析产物：`book.json`、`pages/pXXXX.png|.md|.blocks.json`、`chunks.json`、`manifest.json`（PLAN-002 落地） |
| `<QED_DATA_ROOT>/raw/<domain_id>/<course_id>/*.pdf` | 源书库（只读，QED-Tracker 写入；如 `math-advanced/math_analysis/`） |
| `<QED_DATA_ROOT>/tmp/axiom-flow/<job_id>/` | 模型调用暂存（任务结束清理） |
| `data/` | 过渡期工作目录（落点迁移后退役） |

产物只落 Windows 本地；WSL 不读写 Windows 文件系统（MinerU 容器不挂载 Windows 盘）。

## 故障排查

| 现象 | 处置 |
| --- | --- |
| `stop` 回显 `stopped (forced)` | 优雅信号（CTRL_BREAK）未生效（conda run 等跨 console 语境常见），taskkill 强杀兜底成功——正常现象，无需处理 |
| `stop` 回显 `stop failed: pid …` | 按提示手动 `taskkill /PID <pid> /T /F`，确认进程退出后重试 `stop` 清理 PID 文件 |
| API 返回 503（模型服务不可用） | 确认 MinerU 服务在线：经 8900 `/models/vision/start`（旧名 `/models/mineru/start`）或根仓库 `scripts/image-model/` 脚本；再确认 `QED_OCR_MODEL_URL`（5002）可达 |
| 容器已起但解析慢/超时 | 由根仓库 `scripts/image-model/` 排查 GPU 穿透与 vLLM warmup（首次约 1 分钟）；整本解析超时调大 `AXIOM_OCR_TIMEOUT`（默认 600s） |
| 模型异常 | 由根仓库 `scripts/image-model/` 重建容器/镜像 |
| WSL 不可达 | `wsl -l -v` 确认状态；容器由根仓库 `scripts/image-model/` 脚本恢复 |

## 数据操作分类

日常小规模数据操作（单书导入、重跑单页）为 A 类，git 提交留痕即可；大规模批量（如全部
12 本重解析）或受保护环境操作需按任务生命周期建立 D 类计划（备份 + 回滚 + 差异复核）。
