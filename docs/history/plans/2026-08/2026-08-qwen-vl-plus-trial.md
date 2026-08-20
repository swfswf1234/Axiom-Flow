# 计划 2026-08：qwen-vl-plus 最小闭环实验

状态：Accepted
任务类型：C
最后更新：2026-08-16
关联 ADR：无（本实验为外部模型验证；通过后按治理新增 ADR 接受先导引擎）
关联设计：`docs/design/pdf-parsing-and-rendering.md`（fallback 通道 §组件职责）
关联 Tracker：`docs/trackers/todo.md`（V2-010）
归档判定：关闭后按文档规范选择性保留或归档至 `docs/history/` 对应分类

## 目标与成功标准

- 目标：用户裁决的最小化联调路径（2026-08-16）——先用云 API 图像识别模型
  `qwen-vl-plus` 完成「页图 → 识别 → Markdown/blocks → 前端公式渲染对照」全链路验证；
  先实现功能，再深化（MinerU 接入、质量校验），再本地化（模型部署 WSL）。
- 成功标准（采纳门槛，实验前冻结）：
  1. 样本：Rudin 英文《Principles of Mathematical Analysis》物理页 1-20，源 PDF SHA-256
     `923b831558ed161ee49a68b1381c1301f53aae2c84745fcad0384f24db2ce01e`
     （`D:\coding\QED-Engine\dataset\qed-tracker\raw\books\math-qe\01_math_analysis\01-rudin-en_Principles_of_Mathematical_Analysis.pdf`）。
  2. 模型：`qwen-vl-plus`（阿里百炼，凭据 `AXIOM_API_KEY`）；每页最多 3 次尝试；
     单次 ≤8192 tokens、180s 超时（.env 变量 AXIOM_VISION_MAX_TOKENS /
     AXIOM_MODEL_TIMEOUT_SECONDS）。
  3. 预算：≤30 元，超支即中止并保留已花费记录。
  4. 质量：20 页公式 LaTeX 程序化校验可解析率 ≥90%；人工抽查无整段遗漏、无阅读顺序错乱。
  5. 代码按正式模块落地（TDD），实验通过后 V2-006 直接继承。

## 范围与非目标

- 范围：fallback 客户端（qwen-vl-plus 调用 + Markdown→blocks 归一化）、单测、
  20 页真实调用实验、质量报告、todo 登记。
- 非目标：MinerU 接入（V2-005）、完整编排与 state.sqlite（V2-004）、API 端点（V2-007）、
  前端联调（QED-Engine 侧）、设计文档调整与 ADR（实验通过后单独进行）。

## 前置条件

- `AXIOM_API_KEY` 已配置（.env）；QED_env 环境可用；样本 PDF 在 dataset 只读位置。

## 工作项

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| W1 | fallback 客户端 `src/axiom_flow/fallback/`：页图 → base64 → dashscope 调用 → Markdown；页级重试（限流/超时/5xx，每页最多 3 次）；契约版本进元数据 | 完成（`client.py`，契约版本 `qwen-vl-markdown-v1`） |
| W2 | Markdown→blocks 确定性归一化（heading/paragraph/formula，空行切分，LaTeX 单反斜杠保护） | 完成（`normalize.py`，bbox 实验占位垂直均分） |
| W3 | 单测：mock dashscope（成功/截断/限流/超时） | 完成（`tests/unit/test_fallback.py` 17 测试） |
| W4 | 实验脚本：1-20 页逐页识别 → 落盘 `data/books/01-rudin-trial/`（pXXXX.md + pXXXX.blocks.json） | 完成（`scripts/run-qwen-trial.py`；20 页成功 0 失败） |
| W5 | 质量报告：公式块数/LaTeX 可解析率/空块率/失败页/预算明细；人工抽查 | 完成（`scripts/analyze-trial.py`；人工抽查待用户） |

## 实验结果（2026-08-16）

- 成功 20 页 / 失败 0 页（每页恰好 1 次调用，无重试）；调用 20 次，tokens 35890，
  花费估算 <1 元（远低于预算 ≤30 元，以百炼账单为准）。
- 独立公式块 4（可解析 4，100%）；行内公式 365（可解析 365，100%）；空块率 0.00%。
- 文本长度比（识别文本/原生文本）全部页面 ≥0.7，无低比值页（无整段遗漏信号）。
- 抽查样本（p0013/p0020）：正文、Proposition、行内/独立公式 LaTeX 完整正确。
- 明细：`data/books/01-rudin-trial/quality-report.json` + `trial-summary.json`。

## 验证与验收

- 单元：`pytest tests/unit -q`（fallback 单测全过）。
- 门禁：`pytest tests -q` + `ruff check src tests`。
- 实验：20 页真实调用成功，对照采纳门槛逐项验收（可渲染率 ≥90%、无整段遗漏、预算 ≤30 元）。
- 采纳门槛核对：① 公式程序化可解析率 100%（≥90% ✅）；② 人工抽查无整段遗漏/阅读顺序错乱
  （程序化 text_length_ratio 全部 ≥0.7 ✅，最终人工抽查由用户完成）；③ 预算未超支 ✅（<1 元）。

## 回滚

- 实验产物仅落盘 `data/books/01-rudin-trial/`（可整体删除，不影响正式 book_id）。
- 代码经 git 提交，单一提交可 revert。
- 识别质量不达标时：不升设计，回退为原主线 MinerU 先行（V2-004/005 顺序不变），
  qwen-vl-plus 维持兜底定位。

## 关闭与归档

- 验收通过：更新 todo V2-010 为 Completed，报告随计划正文留档；转入「设计升级」——
  新增 ADR 接受 qwen-vl-plus 为先导引擎，调整主线任务顺序（V2-006 提前）。
- 验收不通过：计划正文标记结果与证据，V2-010 关闭为 Rejected，回退原主线。
