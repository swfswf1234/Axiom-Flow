# 开发指南

状态：Current
最后更新：2026-08-21

## 环境

| 项 | 值 |
| --- | --- |
| Python | 3.12（Anaconda 环境 `QED_env`：`D:\software\anaconda3\envs\QED_env\python.exe`） |
| 包管理 | `pip install -e ".[dev]"`（pyproject.toml 为依赖事实源） |
| 推理服务 | WSL Ubuntu 24.04 + Docker Compose（见 [运维指南](operations.md)） |

## 必需环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `API_KEY` | 统一供应商密钥（替代原 `AXIOM_API_KEY`） | — |
| `QED_API_SELECT` | 调用模式：`local`（自身 API_KEY 直连）或 `qed-engine`（经 8900 网关） | `local` |
| `QED_LLM_GATEWAY_URL` | 8900 网关地址（qed-engine 模式使用） | `http://127.0.0.1:8900` |
| `AXIOM_VISION_MODEL` | 视觉模型名 | `qwen-vl-plus` |
| `AXIOM_MYSQL_*` | MySQL 连接（见 `.env`） | — |

## 常用命令

```powershell
# 安装
& D:\software\anaconda3\envs\QED_env\python.exe -m pip install -e ".[dev]"

# 测试
& D:\software\anaconda3\envs\QED_env\python.exe -m pytest tests -q

# 冒烟（需推理服务在线）
& D:\software\anaconda3\envs\QED_env\python.exe -m pytest tests/smoke -q -m smoke

# 静态检查
& D:\software\anaconda3\envs\QED_env\python.exe -m ruff check src tests

# 启动推理基础设施（vLLM + mineru-api）
powershell -File scripts/infra-up.ps1
```

## 门禁

推送前必须通过：`pytest tests -q` + `ruff check src tests`（本地唯一门禁，无远端 CI）。

## 新增/改动文档

活跃架构、设计、ADR 或 code-map 变更后，运行契约测试（`tests/contract/`）并保持元数据
（设计状态/实现状态/最后更新/关联代码/关联测试/关联 ADR）同步。
