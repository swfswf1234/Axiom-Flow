# 归档设计文档索引

状态：Historical
最后更新：2026-08-21

本目录保存 v0.1 版本文档体系对齐（V2-015，根仓库 ADR 0010 范本）中使命完成、标 Superseded
的设计文档。完整旧文件优先通过不可变 Git commit 恢复；历史资料不得作为当前设计或操作依据。

| 文档 | 原位置 | 归档原因 | 当前承接 |
| --- | --- | --- | --- |
| [PDF 解析与渲染（v2 探索基线）](pdf-parsing-and-rendering.md) | `docs/design/` | v2 探索基线，v0.1 对齐时拆分 | `docs/design/parsing-pipeline.md`（解析管线）、`docs/architecture/api.md`（API 契约） |
| [8902 集成契约与联调对齐](8902-integration-contract.md) | `docs/design/` | 联调契约完成使命，契约正文固定化 | `docs/architecture/api.md` |
| [服务生命周期脚本设计](service-lifecycle.md) | `docs/design/` | 生命周期契约固定化 | `docs/architecture/api.md` ①生命周期与健康 |
| [生命周期脚本 `_pid_is_alive` 编码修复](service-lifecycle-encoding-fix.md) | `docs/design/` | 随 service-lifecycle 一并归档 | `docs/architecture/api.md` ①生命周期与健康 |
| [文档体系范本对齐设计](docs-restructure-alignment.md) | `docs/design/` | 本任务设计文档，执行完毕使命完成 | 本仓库文档体系（ADR 0010 范本） |
