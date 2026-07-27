# Axiom-Flow v0.1 测试指南

## 概览

Phase 1 涵盖 MinerU 集成 + PostgreSQL + FastAPI，测试方案如下：

## 测试结构

```
tests/
├── __init__.py
├── conftest.py              # pytest fixtures (SQLite in-memory + mock MinerU)
├── test_mineru_service.py   # MinerUService 单元测试
├── test_layout_repo.py      # LayoutRepo CRUD 测试
├── test_document_repo.py    # DocumentRepo CRUD 测试
├── test_api_ingest.py       # POST /ingest 端点测试
├── test_api_status.py       # GET /status 端点测试
├── test_api_layout.py       # GET /layout 端点测试
└── test_e2e.py              # 端到端：PDF → 解析 → 入库 → 查询
```

## 运行测试

```bash
# 全部测试（跳过需要 PostgreSQL 的）
python -m pytest tests/ -v

# 含覆盖率
python -m pytest tests/ --cov=app/

# 仅运行 MinerU 测试（无需外部依赖）
python -m pytest tests/test_mineru_service.py -v
```

## 测试覆盖范围（Phase 1 目标）

| 模块 | 测试数 | 验证内容 |
|------|--------|---------|
| `app/services/mineru_service.py` | 5+ | parse_pdf 调用、布局提取、公式提取、错误处理 |
| `app/repository/layout_repo.py` | 3+ | LayoutBlock CRUD、按页查询 |
| `app/repository/document_repo.py` | 3+ | Document CRUD、状态更新 |
| `app/api/ingest.py` | 3+ | 请求验证、异步解析触发、响应格式 |
| `app/api/status.py` | 2+ | 状态查询、404 处理 |
| `app/api/layout.py` | 2+ | 分页布局查询、页码越界 |

## 关键测试技术

### MinerU Mock

```python
# MinerUService 测试：mock MinerU 实际调用
@pytest.fixture
def mineru_service():
    return MinerUService(output_dir="tests/fixtures/parsed")

@pytest.mark.asyncio
async def test_parse_pdf_returns_markdown_and_layout(mineru_service):
    result = await mineru_service.parse_pdf("tests/fixtures/sample.pdf")
    assert result.markdown is not None
    assert result.layout_json is not None
```

### SQLite in-memory 测试 CRUD

```python
# conftest.py: 方式同 QED-Tracker
engine = create_engine("sqlite:///:memory:", echo=False)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)
```

## Phase 1 验收标准

- [ ] 5+ 本教材 PDF 可正常解析
- [ ] Layout 数据精确入库（bbox 无丢失）
- [ ] API 端点返回格式符合规范
- [ ] 测试覆盖率 >= 70%
