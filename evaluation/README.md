# Axiom-Flow 评估专区（evaluation/）

> 状态：Current（v2 占位）——独立评估区域，与主链路（`src/axiom_flow/`）完全隔离，
> 主链路不依赖本目录任何代码。V2 评估体系（质量验收、公式渲染率抽查、回归基线）
> 随 V2-008 验收任务重建，当前仅保留样本数据。

## 定位

- 本目录只做**评估与回归**：样本、fixture、报告；不参与解析主链路（导入/编排/API）。
- v1 时代评测 CLI（`cli.py`、`__main__.py`、`tools/`）已随 V2 推倒重来移除
  （其依赖的 v1 模块已删除，不可运行；git 历史与
  `docs/history/v1-20260814/design/evaluation-governance.md` 可追溯）。
- 运行期评估数据（快照、replay 产物）写入被忽略的 `data/`（运行时目录），不入库。

## 样本清单

| 样本 | 内容 | 用途 |
| --- | --- | --- |
| [数学分析回归样本](documents/数学分析回归样本--2249d79fb6d0/case.json) | CC0 PDF（source.pdf）、replay 与完整期望产物 | 冒烟测试用 PDF（`scripts/smoke-api.sh`）；V2 回归基线候选 |
| [数学分析原理（第 3 版）](documents/数学分析原理-第3版--341544f3fa9c/index.md) | 来源哈希、manifest 与脱敏报告 | v1 私有真实教材质量评估存档（只读参考） |

真实教材 PDF、页图与模型响应不得提交；样本中仅包含可提交的脱敏/公开内容。
