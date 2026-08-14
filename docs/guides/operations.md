# 运维指南

状态：Current
最后更新：2026-08-14

## 推理基础设施（WSL + Docker Compose）

容器化服务：`mineru-api`（MinerU FastAPI 单体，内嵌 vLLM 推理引擎，hybrid-engine 后端，
GPU 穿透）。Milvus 为后续接入占位。启停脚本位于 `scripts/`：

| 脚本 | 作用 |
| --- | --- |
| `infra-up.ps1` | `wsl docker compose up -d`，启动 mineru-api、等待健康、建立 keepalive 会话 |
| `infra-down.ps1` | 优雅停止（`down`）+ 回收 keepalive 会话 |
| `infra-status.ps1` | 容器健康检查 + GPU 可见性 + keepalive 状态 + 端点探测 |
| `infra-reset.ps1` | 删除容器（模型在镜像内，需重建镜像才需重新下载） |

端口：mineru-api **8002**（OpenAPI `/docs`）。模型固化在镜像内（`MINERU_MODEL_SOURCE=local`，
构建时 `mineru-models-download` 拉取，约 4.6GB 缓存），首次构建镜像耗时较长：

```powershell
wsl -e docker build -t mineru:latest -f scripts/docker/Dockerfile scripts/docker/
```

> **WSL 挂起（重要环境知识）**：WSL 2.6.x 在最后一个 wsl 客户端退出后约 30 秒会挂起 VM
> （即使 `.wslconfig` 已设 `vmIdleTimeout=2147483647` 禁用空闲关机），挂起期间容器冻结、
> 端口不通。`infra-up.ps1` 通过常驻 wsl 会话（keepalive，PID 记录于
> `scripts/.keepalive.pid`）防挂起；`infra-down.ps1` 回收。手动调试时若容器无响应，
> 先检查 `infra-status.ps1` 的 keepalive 状态。

> 架构说明：MinerU 3.4 的 router 模式（`mineru-router`，独立 vLLM worker）在本环境实测存在
> worker 502 循环重启问题，不可用；单体 `mineru-api` 已验证端到端解析成功，采用单体。
> 容器不挂载 Windows 盘，文件交互通过 `docker cp` 或 API 上传（multipart 字段名 `files`）。
> 冒烟验证：`wsl docker cp scripts/smoke-api.sh mineru-api:/tmp/smoke.sh` +
> `wsl docker exec mineru-api bash /tmp/smoke.sh`（需先 `docker cp` 一份 PDF 为
> `/tmp/smoke.pdf`）。

## 数据目录

| 路径 | 内容 |
| --- | --- |
| `data/books/<book_id>/` | 每本书的解析产物：`book.json`、`state.sqlite`、`pages/pXXXX.png|.md|.blocks.json`、`manifest.json` |
| 源书库 | `D:\coding\QED-Engine\dataset\qed-tracker\raw\books\math-qe\01_math_analysis`（12 本数学分析书籍，只读） |

产物只落 Windows 本地，WSL 不读写 Windows 文件系统。

## 故障排查

| 现象 | 处置 |
| --- | --- |
| API 返回 503 | 运行 `infra-up.ps1` 后重试；`infra-status.ps1` 检查容器健康 |
| 容器已起但解析慢/超时 | `infra-status.ps1` 确认 GPU 穿透生效（`--gpus all`）；首次请求含 vLLM warmup（约 1 分钟） |
| 模型异常 | `infra-reset.ps1` 删容器后 `infra-up.ps1`；仍异常则重建镜像 |
| WSL 不可达 | `wsl --shutdown` 后重新 `wsl -l -v` 确认状态再 `infra-up.ps1` |

## 数据操作分类

日常小规模数据操作（单书导入、重跑单页）为 A 类，git 提交留痕即可；大规模批量（如全部
12 本重解析）或受保护环境操作需按任务生命周期建立 D 类计划（备份 + 回滚 + 差异复核）。
