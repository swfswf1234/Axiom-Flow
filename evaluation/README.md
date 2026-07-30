# Axiom-Flow 评测模块

本目录同时维护公开确定性回归和私有模型 benchmark，两者不能互相替代。协议见
[`evaluation-governance.md`](../docs/design/evaluation-governance.md)，长期边界见
[ADR 0019](../docs/adr/0019-public-fixture-and-private-benchmark-boundary.md)。

## 公开 fixture 回归

`fixtures/math-sample-v1/` 是项目自制并以 CC0 发布的四页数学 PDF，包含冻结 replay 和标准 schema
v2 解析包。先生成一个临时实际包，再执行完整事实比较：

```powershell
$env:PYTHONPATH = "src"
python -m evaluation.replay --fixture evaluation/fixtures/math-sample-v1 --output-data-dir data/evaluation/replay
$fixture = Get-Content -Raw evaluation/fixtures/math-sample-v1/fixture.json | ConvertFrom-Json
$actual = Join-Path "data/evaluation/replay" $fixture.expected_run
python -m evaluation.regression --fixture evaluation/fixtures/math-sample-v1 --actual $actual --output data/evaluation/replay/report.json
```

`regression` 返回 `0` 表示全部通过，`1` 表示事实比较失败，`2` 表示参数、fixture 或产物契约非法。
重建仓库 fixture 使用 `python -m evaluation.fixture_builder --output evaluation/fixtures/math-sample-v1`，
重建后必须人工复核 PDF、源稿、许可和金标差异。

## 私有模型 benchmark

Rudin 冻结样本与既有脱敏证据见 [`benchmarks/private/rudin/`](benchmarks/private/rudin/index.md)。
原 PDF、页图、完整响应和密钥不得提交。正式运行顺序如下：

```powershell
python -m evaluation.preflight --pdf <本地-PDF> --page-no 1
python -m evaluation.benchmark --source <本地-PDF> --manifest <manifest.json> --output-dir data/evaluation/runs/<experiment-id>
python -m evaluation.scorecard --manifest <manifest.json> --results <人工评分.json> --output <脱敏-scorecard.json>
```

预检通过只表示链路可用。12 页 scorecard 通过且后续 ADR 接受后，候选才具备模型质量证据；
20 页连续工程试跑不能代替该门禁。
