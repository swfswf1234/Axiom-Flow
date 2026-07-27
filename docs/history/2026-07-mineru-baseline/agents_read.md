# 📜 Axiom-Flow: Agentic Operations Protocol (AOP)

> **项目定位**：QED-Engine 的核心知识解析与重构引擎（MinerU + LlamaIndex）。
> **AOP 协议版本**：1.0 (Production-Ready)
> **项目版本**：0.1 (Phase 1: MinerU Integration)
> **状态**：Active

**重要提示**：所有 AI Agent 在执行任务前必须完整阅读此文档。保持 `design/`、`trackers/` 和 `worklogs/` 的实时同步是 Agent 的首要责任，而非人类。

## 1. 目录职责与 SSoT (唯一事实来源)

| 目录 | 职责说明 | 强制规则 |
| --- | --- | --- |
| **`design/`** | 架构与设计 | 存放解析 Pipeline、数学 Schema、RAG 检索流。修改核心逻辑前必须更新此目录。 |
| **`discuss/`** | 决策讨论 | 存放 OCR 识别策略对比（如 MinerU vs Marker）或复杂逻辑分块的方案讨论。 |
| **`trackers/`** | 状态追踪 | 仅维护 `todos.md`。任务状态分为：`[Extraction]`、`[Transformation]`、`[Indexing]`。 |
| **`worklogs/`** | 操作日志 | 每日任务必须记录。格式：`YYYY-MM-DD.md`。记录 Actions, Verification, Docs_Update。 |
| **`knowledge_base/`** | 数字化清单 | 维护 `inventory.md`（书目处理状态）和 `schema.json`（版面坐标定义）。 |

## 2. 核心工作流：P-E-V-L 循环

Agent 在处理任务时必须严格遵守四个闭环阶段：

### 2.1 计划 (Plan)

* 动作：在开始任务前，在对话中输出“执行预览”。
* 内容：分析当前系统状态、拟修改的文件、潜在的 OCR 语法冲突风险。

### 2.2 执行 (Execute)

* **代码规范**：使用 Python FastAPI 异步框架；所有注释必须使用 **英文**。
* **数学一致性**：处理数学公式时，必须确保 LaTeX 输出符合 `$$...$$` 格式，严禁随意转义。
* **元数据保留**：任何解析步骤都**禁止丢弃**物理坐标（bbox）和页码（page_no）。

### 2.3 验证 (Verify)

* **强制自检**：代码完成后，必须运行 `pytest` 或针对单页 PDF 的 `dry_run` 脚本。
* **结果取证**：验证成功的 Log 片段或解析后的 Markdown 样例必须记录在 `worklogs/`。

### 2.4 日志 (Log)

* **状态迁移**：将 `trackers/todos.md` 中的项移动到“已完成”区域，并标注对应的 Commit Hash。
* **文档沉淀**：如果任务涉及接口变动，必须同步更新 `design/api_spec.md`。

## 3. 数学解析专项约束 (Axiom-Flow Specific)

1. **Layout-First**：解析结果不只是文本，必须是带布局信息的结构化 JSON。
2. **Atomicity (原子性)**：一个知识块（如 Theorem 1.1）必须作为一个原子节点存储，严禁在中间位置进行物理切分。
3. **Latex Sanitization**：遇到无法确定的公式识别错误，必须标记 `[CHECK_REQUIRED]`，不准通过“幻觉”脑补公式。

## 4. Git 与提交规范

* **Commit 格式**：`<type>: <description>`。
* `feat`: 新解析能力；`fix`: 修复 OCR 或 RAG 错误；`docs`: 文档更新；`refactor`: 架构优化。


* **提交前置要求**：Agent 在 Commit 前必须自检是否已完成对应的 `worklogs/` 更新。

## 5. Agent 启动引导 (System Prompt Extension)

当你在本项目中初始化 Agent 时，请使用如下指令：

> "你现在是 Axiom-Flow 的高级 Agent。请读取项目根目录的 `agents_read.md`。
> 1. 检查 `trackers/todos.md` 确认当前最高优先级任务。
> 2. 检查当日 `worklogs/`。
> 3. 在执行任何文件修改前，请输出你的 P-E-V-L 计划。"
