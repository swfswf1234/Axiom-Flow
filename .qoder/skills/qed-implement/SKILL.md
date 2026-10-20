---
name: qed-implement
description: Use when implementing code in the QED repos — locates the module via the repo's standard mapping (code-map), enforces TDD and the test isolation rules. Trigger on 实现, 编码, 写代码, 修改代码.
---

# QED 编码实现（code-map 定位 + TDD）

## 步骤

1. **定位模块**：在标准映射「模块映射」（`docs/architecture/code-map.md`）找到模块职责、状态、设计关联（DesignRef）与关联测试；契约变化先完成 ADR/设计，再改实现、code-map 与语义测试。
2. **先写测试**：涉及契约/状态机/迁移/行为变化，先写会失败的测试（调用 `test-driven-development`），再写实现使其通过。
3. **遵循分层**：按标准映射「测试门禁」的测试分层与治理契约测试规范。
4. **同步映射**：模块新增/移动/删除时，同一变更内同步 code-map 与关联设计文档；先用 `rg "旧路径"` 全库确认无残余引用。
5. **验证**：运行 `qed-closeout` 中列出的门禁。

## 隔离铁律（必须遵守）

- 默认测试不访问公网、不要求 API key、不调用外部模型服务、不写运行数据库。唯一例外
  （`QED_TEST_MYSQL=1` → `qed_test` 库自有 `af_*` 表族）与完整边界以
  `docs/standards/testing.md`「测试编写约定」为准，改 orchestrator/db 持久化后须手动跑 MySQL 层。
- 测试与被测代码保持同一目录结构命名，只用临时目录作数据根，禁止读写真实数据根。
- TLS 校验默认开启，只能由用户显式配置关闭。
- 仓库相对路径用 `Path(__file__).resolve().parents[N]` 推导，不依赖工作目录。

## 调试

遇到 bug 或测试失败，先调用 `systematic-debugging` 定位根因，禁止猜测性修复；可复现缺陷先补回归测试再修复。
