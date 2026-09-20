# 数据库设计（Axiom-Flow af_* 命名空间）

设计状态：Accepted
确认状态：暂定
实现状态：Current（四表一次 Alembic 迁移 + 仓储层已实现；任务/页表语义按 PLAN-003 契约修正）
最后更新：2026-09-20
关联代码：`src/axiom_flow/db/`（models/repositories）、`src/axiom_flow/migrations/`（Alembic 迁移）
关联测试：`tests/unit/test_db.py`、`tests/unit/test_db_migrations.py`、`tests/contract/test_architecture_documents.py`（本文档守护）
关联 ADR：`docs/adr/0001-v2-exploration-direction.md`、`docs/adr/0004-model-boundary-and-pipeline-ownership.md`、根仓库 [ADR 0003](https://github.com/QED-Engine/qed-engine/blob/main/docs/history/adr/v0.1/0003-shared-qed-database-independence.md)（共享 qed 库独立性例外）

> 本文件是 **Axiom-Flow 的固定数据库设计文档**（根仓库 ADR 0010 范本 / REQ-047）：登记
> Axiom-Flow 在共享 MySQL `qed` 库中的私有表（`af_*` 命名空间）设计。根仓库总纲
> （QED-Engine `docs/architecture/database-design.md`）只维护命名空间隔离规则，**不复制本文件
> 的表结构**。**ARCH-020 重构（2026-09-14）**将 af_* 由原两表草案
> （af_books/af_block_reviews）重定义为四表。

## 目的与边界

- MySQL 8，库名 `qed`，三项目共用同一实例与库（根仓库 ADR 0003 独立性铁律的明确例外）。
- 表命名空间隔离：QED-Tracker 使用 `qt_*` 前缀，Axiom-Flow 使用 `af_*` 前缀；**互不读取对方
  表**。共享 `qed_*` 前缀表族（qed_domain/qed_course/qed_llm_calls 等）所有权 QED-Tracker，
  本仓库只读不写（`qed_llm_calls` 例外：三项目均可写入调用记录）。
- af_* 表为 Axiom-Flow **私有**：只从 qt_books 同步已验证书行的只读元数据，任何外部故障/
  变更不影响本表读取；Axiom-Flow 用 Alembic 独立初始化自己的表，不依赖其他项目迁移。
- 凭据与库名唯一事实源：根 `.env` 的 `QED_DB_*` 变量；密码值绝不下发。
- **入库边界**：文件系统承载产物正本（`parsed/` 页图/Markdown/blocks/分块），DB 只存元数据与
  状态；`chunks.json` 为派生文件，**不入库**（可重建）。

## 表清单总览

| 表 | 前缀 | 一行 = | 状态 | 事实源 |
| --- | --- | --- | --- | --- |
| `af_books` | af_*（私有） | 一册已验证书（qt_books 只读快照 + ingest/解析进度，本侧维护） | **已实现**（真机已建表，ARCH-013/014/015 关闭） | 本文档 |
| `af_parse_jobs` | af_*（私有） | 一次解析任务（逐页/全本，状态与进度） | **已实现**（真机已建表；异步语义收尾于 PLAN-003 W8） | 本文档 |
| `af_pages` | af_*（私有） | 一页解析状态与质量信号 | **已实现**（真机已建表；页级持久化 PLAN-003 W2） | 本文档 |
| `af_block_edits` | af_*（私有） | 一次块级人工编辑（判定/备注/文字/范围） | **已实现**（真机已建表；块下标重映射待 REQ-080） | 本文档 |

```mermaid
erDiagram
    af_books ||--o{ af_parse_jobs : "book_id"
    af_books ||--o{ af_pages : "book_id"
    af_books ||--o{ af_block_edits : "book_id"
    af_books {
        varchar book_id PK "同源 qt_books.book_id"
        varchar domain_id
        varchar course_id
        varchar course_name
        varchar knowledge_id
        varchar title
        varchar part
        varchar display_title
        json authors
        varchar file_path "数据根相对路径"
        int page_count
        varchar sha256
        varchar ingest_status
        varchar parse_status
        int pages_done
        text notes
    }
    af_parse_jobs {
        varchar job_id PK
        varchar book_id FK
        json pages "NULL=全书"
        varchar engine
        varchar status
        json progress
        varchar error
    }
    af_pages {
        varchar book_id PK
        int page_no PK
        varchar status
        varchar source
        json quality
        varchar error
        datetime parsed_at
    }
    af_block_edits {
        varchar edit_id PK
        varchar book_id FK
        int page_no
        int block_index
        varchar block_type
        varchar verdict
        varchar note
        text corrected_text
        json corrected_bbox
    }
```

## af_books（书目表，一行 = 一册已验证书）

```sql
CREATE TABLE af_books (
  book_id       VARCHAR(100) NOT NULL,          -- PK：同源 qt_books.book_id
  domain_id     VARCHAR(32)  NOT NULL DEFAULT '',
  course_id     VARCHAR(64)  NOT NULL DEFAULT '',
  course_name   VARCHAR(200) NOT NULL DEFAULT '',
  knowledge_id  VARCHAR(100) NOT NULL DEFAULT '',
  title         VARCHAR(500) NOT NULL DEFAULT '',
  part          VARCHAR(32)  NOT NULL DEFAULT '',
  display_title VARCHAR(500) NOT NULL DEFAULT '',
  authors       JSON         NOT NULL,          -- list[{name, role}]
  file_path     VARCHAR(512) NOT NULL DEFAULT '',-- 数据根相对路径（同源 qt_books.file_path）
  page_count    INT          NULL,              -- ingest 时计算
  sha256        VARCHAR(64)  NOT NULL DEFAULT '',-- ingest 时计算
  ingest_status VARCHAR(16)  NOT NULL DEFAULT 'none',   -- none/ingesting/ingested/failed
  parse_status  VARCHAR(24)  NOT NULL DEFAULT 'pending',-- pending/parsing/completed/failed
  pages_done    INT          NOT NULL DEFAULT 0,
  notes         TEXT         NULL,
  synced_at     DATETIME     NOT NULL,
  updated_at    DATETIME     NOT NULL,
  PRIMARY KEY (book_id),
  KEY ix_af_books_course (course_id),
  KEY ix_af_books_status (parse_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='书目同步表：QED-Tracker 已验证书目的只读快照 + ingest/解析进度（Axiom-Flow 私有）';
```

### 同步语义

- **幂等**：同步以 `book_id` 为幂等键，重复同步只 upsert 不产生重复行；qt_books 删除/变更
  不影响已同步行（快照语义）。
- **进度自持**：`ingest_status`/`parse_status`/`pages_done` 由 Axiom-Flow 侧维护（ingest 与
  解析任务回写），不依赖 qt_books 之外的上游字段。
- **重新 ingest 回置进度**：`POST /books/{id}/ingest` 成功替换产物后，把 `parse_status` 置回
  `pending`、`pages_done` 归零，并清空该书 `af_pages` 行（旧产物已作废，不保留过期页状态）。
- **字段冗余**：课程归属（domain_id/course_id/course_name）冗余进表，前端只消费 8902
  `/books` 即可建树（8901 离线不影响树展示）。

## af_parse_jobs（解析任务表，一行 = 一次解析任务）

```sql
CREATE TABLE af_parse_jobs (
  job_id      VARCHAR(64)  NOT NULL,            -- PK
  book_id     VARCHAR(100) NOT NULL,            -- → af_books.book_id
  pages       JSON         NULL,                -- NULL = 全书；否则页码数组
  engine      VARCHAR(24)  NOT NULL DEFAULT 'mineru',
  status      VARCHAR(16)  NOT NULL,            -- queued/running/completed/failed
  progress    JSON         NOT NULL,            -- {"parsed":N,"total":M,"error":""}
  error       VARCHAR(500) NOT NULL DEFAULT '',
  created_at  DATETIME     NOT NULL,
  started_at  DATETIME     NULL,
  finished_at DATETIME     NULL,
  PRIMARY KEY (job_id),
  KEY ix_af_parse_jobs_book (book_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='解析任务表：逐页/全本解析任务状态与进度（Axiom-Flow 私有）';
```

### 任务表语义

- **`engine` 存解析后的实际引擎名**：请求体 `engine` 优先、缺省回落 `AXIOM_OCR_ENGINE`，任务行
  记录最终执行所用引擎（未实现/未注册名称在提交期即报错，**不会**落行、也不回落）。
- **错误双载体分工**：`error`（≤500 字符）= 终态摘要；`progress.error`（≤200 字符）= 进行中的
  失败页摘要。两者可同时存在，任务进入终态后以 `error` 为准。
- **`progress` 写入节流**：距上次写入 ≥1 秒或每处理满 10 页覆写一次，终态必写一次，单页失败节点
  强制写入；因此运行中 `GET /parse-jobs/{id}` 的 `progress.parsed` 单调不减但可能滞后。
- **提交期 503 不落行**：模型服务探活失败发生在落库之前，故 503 场景不产生 `af_parse_jobs` 行；
  受理后的故障才记为 `failed`。
- **删除保留审计**：`DELETE /books/{id}`（含 `?purge=true`）不清理历史任务行。

## af_pages（页状态表，一行 = 一页解析状态）

```sql
CREATE TABLE af_pages (
  book_id   VARCHAR(100) NOT NULL,
  page_no   INT          NOT NULL,
  status    VARCHAR(16)  NOT NULL DEFAULT 'pending', -- pending/parsed/failed
  source    VARCHAR(24)  NOT NULL DEFAULT '',        -- 实际引擎标识
  quality   JSON         NULL,                       -- 质量信号
  error     VARCHAR(500) NOT NULL DEFAULT '',
  parsed_at DATETIME     NULL,
  PRIMARY KEY (book_id, page_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='页状态表：逐页解析状态与质量信号（Axiom-Flow 私有）';
```

### 页表语义

- **`pending` 以「无行」表示**：不为未解析页预建占位行；`status` 实际只写 `parsed` / `failed`，
  断点续跑的集合 = 无行页 ∪ `failed` 页。
- **持久化不变式**：每页成功/失败即 `commit`，且**产物写先于页行提交**，故「有 af_pages 行必有
  对应产物文件」；整本失败时已解析页的行与产物保留可重跑。
- **重复 ingest 清空**：重新 ingest 会原子替换产物目录并删除该书全部页行（解析状态回置
  `pending`），使「无行即 pending」的表示法不产生残留误判。

## af_block_edits（块编辑表，一行 = 一次块级人工编辑）

```sql
CREATE TABLE af_block_edits (
  edit_id        VARCHAR(100) NOT NULL,         -- PK：ed_<md5(book_id:page_no:block_index)>
  book_id        VARCHAR(100) NOT NULL,         -- → af_books.book_id；索引
  page_no        INT          NOT NULL,
  block_index    INT          NOT NULL,         -- blocks 数组下标（0 基）
  block_type     VARCHAR(24)  NOT NULL DEFAULT '',
  verdict        VARCHAR(8)   NOT NULL DEFAULT '',  -- ''=未判定 / ok / bad
  note           VARCHAR(1000) NOT NULL DEFAULT '',
  corrected_text TEXT         NULL,             -- 文字修正（公式为 LaTeX 源码）
  corrected_bbox JSON         NULL,             -- [x0,y0,x1,y1] 修正范围
  edited_at      DATETIME     NOT NULL,
  updated_at     DATETIME     NOT NULL,
  PRIMARY KEY (edit_id),
  UNIQUE KEY uq_af_block_edits_pos (book_id, page_no, block_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='块编辑表：对照视图中人工判定/备注/文字与范围修正（Axiom-Flow 私有）';
```

- 同块重复编辑 = 覆盖更新（幂等，UNIQUE 键 + upsert）。
- 人工编辑为**覆盖层**：解析产物文件（blocks.json）为模型产物事实源，编辑不入文件，API
  返回时合并。

## 迁移与维护

- `qed` 库由各项目 Alembic **独立初始化**自己的表，互不影响；`qed_*` 共享表由 QED-Tracker
  Alembic 建表与维护（本仓库只读）。
- 新增表或字段变化：先更新本文档与对应契约测试，再实现迁移（根仓库 REQ-047 数据库设计
  长期任务）。
- 实现边界：四表由迁移 `20260917_0001` 建于真机 `qed` 库（2026-09-20，ARCH-013），本文档是
  当前 DDL 的事实源；字段变化先改本文档与契约测试，再出迁移。
- 已登记的后续 DDL：解析产物版本划分（REQ-080：`versions/<job_id>/` + 生效版本指针 +
  `af_parse_jobs` 新列）拆 PLAN-004 承接，其列定义本轮暂不收录。

## 敏感字段规则

- 凭据（`QED_DB_PASSWORD` 等）绝不下发到任何接口响应、日志或异常信息；本仓库不暴露数据库
  连接端点，数据库能力由 8900 `/config/database` 启动快照统一呈现（配置域契约归根仓库）。

## 验证

- `tests/contract/test_architecture_documents.py` 全绿（本文档守护：表清单、命名空间声明）。
- ARCH-002 落地后：Alembic 迁移可重复执行（升级→降级→升级）；sync 幂等（重复同步不产生重复
  行）；edit upsert 覆盖；8901/8902 离线降级不影响本表读取。
