# Axiom-Flow 解析评估

评估按文档组织版本化定义、中性 ParseRun 快照、单运行质量和版本回归。当前契约见
[解析评估设计](../docs/design/evaluation-governance.md)，长期决定见
[ADR 0019](../docs/adr/0019-public-fixture-and-private-benchmark-boundary.md)、
[ADR 0020](../docs/adr/0020-document-centric-evaluation-workspace.md)和
[ADR 0022](../docs/adr/0022-neutral-evaluation-snapshots-and-assessments.md)。

## 文档案例

| 文档 | 可提交内容 | 用途 |
| --- | --- | --- |
| [数学分析回归样本](documents/数学分析回归样本--2249d79fb6d0/case.json) | CC0 PDF、replay 和完整期望产物 | 无模型工程回归。 |
| [数学分析原理（第 3 版）](documents/数学分析原理-第3版--341544f3fa9c/index.md) | 来源哈希、manifest 与脱敏报告 | 私有真实教材质量评估。 |

真实教材 PDF、页图和模型响应不得提交。生产 ParseRun 使用 `AXIOM_DATA_DIR`，冻结评估工作区使用
`AXIOM_EVALUATION_DATA_DIR`；两者可以指向不同的隔离目录。

## 生产主链

先在独立终端启动 Worker，再由 CLI 提交并等待持久 Job：

```powershell
$env:PYTHONPATH = "src"
python -m axiom_flow.worker
python -m evaluation document list
python -m evaluation run --document <case-id> --source <pdf> --label trial-v1 --manifest <manifest>
python -m evaluation assess --document <case-id> --snapshot <snapshot-id> --manifest <manifest>
python -m evaluation report --assessment <assessment-id>
```

`run` 不执行 Worker。等待超时会返回仍可恢复的 Job ID；任务完成后可使用：

```powershell
python -m evaluation capture --document <case-id> --parse-run <run-id> --label resumed-v1
```

快照不携带 baseline/candidate 角色。只有 `baseline_eligible=true` 的干净 main 快照可以在比较时作为
baseline：

```powershell
python -m evaluation compare --document <case-id> --baseline <snapshot-id> --candidate <snapshot-id>
python -m evaluation report --comparison <comparison-id>
```

## 公开 fixture

公开 fixture 回放与完整事实比较不需要模型密钥：

```powershell
$case = "evaluation/documents/数学分析回归样本--2249d79fb6d0"
python -m evaluation.tools.replay --fixture $case --output-data-dir data/evaluation/replay
$fixture = Get-Content -Raw "$case/fixture.json" | ConvertFrom-Json
$actual = Join-Path "data/evaluation/replay" $fixture.expected_run
python -m evaluation.tools.regression --fixture $case --actual $actual --output data/evaluation/replay/report.json
```

重建项目自有 fixture 使用 `python -m evaluation.tools.fixture_builder`。所有真实连通性检查也走生产
Job/Worker，不存在直接 Provider 预检入口。
