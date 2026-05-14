# 环境搭建指南

## 前置条件

| 组件 | 版本 | 状态 |
|------|------|------|
| Conda | >= 22.x | ✅ 已就绪 (`QED_env`) |
| Python | 3.12 | ✅ 已就绪 |
| PostgreSQL | >= 14 | ✅ 已就绪 |

## 环境确认

```bash
conda activate QED_env
python --version           # 应显示 Python 3.12.x
psql --version             # 应显示 psql 14+
```

## 依赖安装

```bash
# 从根目录
pip install -r requirements.txt
```

#> **注意**：MinerU 对扫描版 PDF 需要 OCR 引擎支持。如果待解析 PDF 为扫描件（非电子版），需额外安装 PaddleOCR。参考 `D:\coding\xqfm_ai_server` 的 OCR 部署方案。

## 核心依赖清单

```
# PDF 解析
magic-pdf>=0.7.0          # MinerU 核心库
pymupdf>=1.24.0           # PyMuPDF 解析引擎

# OCR (扫描版 PDF 时需要)
# paddlepaddle-gpu>=2.6.0   # GPU 版
# paddleocr>=2.7.0          # OCR 识别引擎

# RAG 与索引 (Phase 2)
# llama-index>=0.10.0
# llama-index-vector-stores-qdrant>=0.1.0

# 向量存储 (Phase 2)
# qdrant-client>=1.9.0

# Web 框架
fastapi>=0.110.0
uvicorn[standard]>=0.27.0

# 数据库
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
asyncpg>=0.29.0           # 异步 PostgreSQL 驱动

# 任务队列 (Phase 3)
# redis>=5.0.0
# celery>=5.3.0

# 工具
pydantic>=2.0.0
pydantic-settings>=2.0.0
loguru>=0.7.0
httpx>=0.28.0
```

## PostgreSQL 配置

### 建库

```bash
psql -U postgres -c "CREATE DATABASE axiom_flow;"
```

### 建表

```bash
python scripts/init_db.py
```

### 连接配置

在项目根目录创建 `setting.ini`：

```ini
[postgresql]
host = localhost
port = 5432
database = axiom_flow
user = postgres
password = your_password

[qdrant]
host = localhost
port = 6333

[redis]
host = localhost
port = 6379

[mineru]
output_dir = data/parsed
```

## 快速启动

```bash
# 1. 激活环境
conda activate QED_env

# 2. 启动 PostgreSQL（如未启动）
net start postgresql-x64-14

# 3. 初始化数据库
python scripts/init_db.py

# 4. 启动开发服务器
uvicorn app.main:app --reload --port 8002
```

## 数据目录

| 目录 | 用途 |
|------|------|
| `data/raw/` | 原始 PDF 文件 |
| `data/parsed/` | MinerU 解析输出 (md/json/images) |
| `data/exports/` | 导出数据 |
