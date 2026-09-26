# 本地开发环境

状态：Current
确认状态：已确认
最后更新：2026-09-17
治理对象：本地机器标识、环境依赖、构建命令与开发约定（仅机器绑定事实）
依据：QED-Tracker `docs/standards/local-dev.md` 治理模式（[ADR 0003](../adr/0003-standards-governance-alignment.md)）
关联测试：无

## 目的与边界

本文档标注 Axiom-Flow 本地开发环境的机器绑定事实，供开发者快速确认环境一致性、减少重复
探索与误判。本文档仅在 UUID 为 `2C6ECD2C-BBEE-11ED-8A95-F0D4154ABBA8` 的机器上生效；
其他环境需复制并修改。

**本地 vs 可移植边界**：本文只登记机器绑定事实（机器标识、绝对路径、conda 环境名、端口）；
可移植配置以仓库内文件为唯一事实源——Python 版本与依赖看 `pyproject.toml`，运行与数据操作看
[开发指南](../guides/development.md)与[运维指南](../guides/operations.md)。可移植事实与本文
冲突时，以仓库内文件为准并回修本文。

## 机器标识

| 项目 | 值 |
| --- | --- |
| UUID | `2C6ECD2C-BBEE-11ED-8A95-F0D4154ABBA8` |
| 主机名 | `WENFU` |
| 仓库路径 | `D:\coding\QED-Engine\Axiom-Flow` |
| Shell | PowerShell 5.1（Windows） |

## Python 环境

| 项目 | 值 |
| --- | --- |
| Conda 环境 | `QED_env` |
| 环境路径 | `D:\software\anaconda3\envs\QED_env` |
| Python 版本 | 3.12（以 `pyproject.toml` `requires-python` 为准） |

命令统一经环境内解释器执行（与[开发指南](../guides/development.md)一致）：

```powershell
& D:\software\anaconda3\envs\QED_env\python.exe -m <命令>
```

Windows 文件系统大小写不敏感，历史文档曾写作 `qed_env`，与本仓库 `QED_env` 指向同一环境。

## 服务端口速查

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| Axiom-Flow API | 8902 | 本仓库 FastAPI 服务（8000 旧端口兼容保留） |
| MinerU 解析服务 | 5002 | WSL 容器 `mineru-api`，健康端点 `/health`（生命周期与调参由 8900 托管，本仓库只连接 `QED_OCR_MODEL_URL`） |
| QED-Tracker | 8901 | 同级子仓库（本仓库只读，不复制其细节） |
| QED-Engine 后端 | 8900 | 根仓库（模型生命周期与调参托管；本仓库解析直连模型服务，不经其网关） |
| QED-Engine 前端 | 8903 | 根仓库 |

## 推理基础设施

- WSL Ubuntu 24.04 + Docker Compose：`mineru-api`（MinerU FastAPI 单体，内嵌 vLLM，
  hybrid-engine 后端，需 GPU 穿透）；容器与启停脚本由根仓库 `scripts/image-model/` 托管，
  启停/探针/资源互斥/调参由 8900 统一管理，本仓库只连接 `QED_OCR_MODEL_URL`
  （默认 `http://127.0.0.1:5002`，健康端点 `/health`）。
- 详细操作与排障见[运维指南](../guides/operations.md)与根仓库 `scripts/image-model/`。

## 常见误判注记

- **API 503 / MinerU 未起**：经 8900 `/models/vision` 或根仓库 `scripts/image-model/` 启动
  mineru-api 容器（5002）后重试；首次请求含 vLLM warmup（约 1 分钟）。
- **配置语境（2026-09-17 简化）**：Axiom-Flow 只做「模型 API 调用 + 预处理/后处理」，配置
  三段式（服务 / 数据与库 / 解析引擎）；服务脚本无 `--mode`，配置由 `config.py` 向上走查
  `.env` 自读（`AXIOM_PORT`、`QED_DATA_ROOT`【**必填**：缺配、指向仓内 `data/` 或目录不存在
  即启动期报错，ADR 0009】、`QED_DB_*`、`AXIOM_OCR_ENGINE`、
  `QED_OCR_MODEL_URL`、`AXIOM_OCR_TIMEOUT`、`AXIOM_PARSE_WINDOW_SIZE`）。
- **解析超时**：全本按 `AXIOM_PARSE_WINDOW_SIZE`（默认 56 页/块）**串行分块**提交，超时读
  `AXIOM_OCR_TIMEOUT`（默认 600s）且**每次调用独立计**，故整本挂钟上限 = 块数 × 600s、单块失败只损失
  该块页；模型调参仍在 QED-Engine 侧（模型服务配置），本仓库不暴露调参入口。
- **`.env` 值内联注释**：值中 ` #`（空白+井号）会被视为注释并剥离；带 `#` 的真实值请确保
  `#` 前不留空格。
- **停止脚本回显**：`stop` 回显 `stopped (forced)` 表示 taskkill 强杀兜底成功（conda run 等
  跨 console 语境常见），属正常现象；`stop failed` 需按提示手动 `taskkill /PID <pid> /T /F`。

## 变更与取代

环境变更时更新本文档对应字段；机器迁移时复制本文档并修改 UUID 和主机名。
