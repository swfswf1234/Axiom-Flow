# 依赖安装入口简化

设计状态：Accepted
实现状态：Completed
最后更新：2026-07-29

## 目标与范围

删除冗余的 `requirements-dev.txt`，保留 `pyproject.toml` 作为依赖事实源和 `requirements.txt`
作为运行安装兼容入口。本计划不改变依赖版本、运行行为或外部协议。

## 工作项

- [x] 将开发安装入口统一为 `python -m pip install -e ".[dev]"`。
- [x] 删除 `requirements-dev.txt` 并增加结构门禁。
- [x] 通过安装元数据、文档专项和全量本地门禁。

## 验证

editable 安装 dry-run 通过；文档结构、Markdown 链接和代码映射专项测试共 13 项通过；Ruff、
全量 Pytest 62 项、JavaScript 语法和 `git diff --check` 通过。

## 回滚与完成条件

回滚锚点为 `7287ed6`。活跃文档不再引用 `requirements-dev.txt`，安装元数据和全部适用门禁
已经通过，本计划移入历史计划目录。
