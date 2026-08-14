# 运维指南

状态：Current
最后更新：2026-08-14

## 推理基础设施（WSL + Docker Compose）

容器化服务：`mineru-api`（MinerU 编排服务）与 `vLLM`（MinerU2.5 VLM 推理，GPU 穿透）。
Milvus 为后续接入占位。启停脚本位于 `scripts/`：

| 脚本 | 作用 |
| --- | --- |
| `infra-up.ps1` | `wsl docker compose up -d`，启动 vLLM + mineru-api |
| `infra-down.ps1` | 优雅停止（保留卷，模型缓存不丢） |
| `infra-status.ps1` | 容器健康检查 + GPU 可见性 |
| `infra-reset.ps1` | 显式清卷（删除模型缓存，需重新下载） |

端口：vLLM 8000（OpenAI 兼容）、mineru-api 8002。首次启动会自动下载模型，耗时较长；
后续启动使用 WSL 卷缓存，无需重复下载。

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
| 容器已起但解析慢/超时 | `infra-status.ps1` 确认 GPU 穿透生效（`--gpus all`） |
| 模型异常 | `infra-reset.ps1` 清卷后重新拉取 |
| WSL 不可达 | `wsl --shutdown` 后重新 `wsl -l -v` 确认状态再 `infra-up.ps1` |

## 数据操作分类

日常小规模数据操作（单书导入、重跑单页）为 A 类，git 提交留痕即可；大规模批量（如全部
12 本重解析）或受保护环境操作需按任务生命周期建立 D 类计划（备份 + 回滚 + 差异复核）。
