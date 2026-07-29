# 回归待办

状态：Current
最后更新：2026-07-29

| ID | 现象 | 证据 | 本地状态 | 关闭条件 |
| --- | --- | --- | --- | --- |
| REG-001 | `main@a6ec4e0` 的 GitHub Actions Pytest 步骤失败 | Actions run `30350570456`；Ruff、JavaScript 和 `git diff --check` 成功 | Windows 本地 54 passed | 获取失败测试详情、增加回归覆盖并使同一 CI 工作流通过 |

REG-001 暂不作为文档整理的阻塞项；进入 `tests/` 与 `.github/` 目录对齐时必须优先关闭。
