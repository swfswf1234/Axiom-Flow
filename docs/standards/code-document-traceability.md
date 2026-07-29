# 文档与代码双向追溯规范

状态：Current
最后更新：2026-07-29
治理对象：code-map、模块 DesignRef、架构/设计关联和语义同步门禁
依据 ADR：`docs/adr/0015-standards-as-governance-source.md`、`docs/adr/0018-src-package-and-application-owned-workflows.md`
关联测试：`tests/test_code_document_mapping.py`、`tests/test_architecture_documents.py`、`tests/test_design_documents.py`

## 目的与边界

本标准让人工和 Agent 能从代码定位设计依据，也能从设计定位实际代码与测试。
`docs/architecture/code-map.md` 是代码、设计与测试映射的唯一事实源；文件头 DesignRef 只用于
阅读代码时反查，不是第二份映射表。文档分类查[文档规范](documentation.md)，决策准入查
[ADR 治理](adr-governance.md)。

## 强制规则

### code-map 与文件头

- 所有受管且非豁免模块必须在 code-map 恰好登记一次；新增、移动、删除模块或改变 DesignRef
  时，同步更新 code-map、文件头和关联测试。
- 非豁免 Python 模块在首个 import 前使用中文模块 docstring，声明模块职责、
  `设计关联（DesignRef）` 和实现状态；测试模块还声明被测代码。
- `__init__.py`、纯常量和极短无业务语义文件可豁免，但不得承载业务规则。
- 外部工具存在编码约束的配置文件使用 ASCII `DesignRef:` 和 `Status:`；中文职责保留在
  code-map 与关联设计。
- Legacy 模块只能指向 `docs/history/`，并明确不属于当前目标架构；Current 模块不得指向 History。

推荐 Python 文件头：

```python
"""
模块职责：说明本模块承担的业务或技术责任。
设计关联（DesignRef）：docs/design/example.md
实现状态：Current
"""
```

### 架构同步

- 活跃架构声明关联代码、关联测试和关联 ADR；无实现时写“尚未实现”，不得指向无关代码。
- 运行组件、包依赖、领域状态、事实来源、能力归属或 DesignRef 变化时，同步正文、Mermaid、
  code-map 和架构语义测试。
- Accepted 约束与当前实现有偏差时，使用稳定 `ARCH-NNN` 在架构符合度和 tracker 双向登记，文档
  实现状态不得写成完全实现。

### 设计同步

- Design 按可执行流程划分，描述输入输出、接口与数据、状态、失败语义和当前符合度；没有代码
  所有权的概念说明不得单独成为活跃设计。
- 接口字段、审阅/任务状态、工作簿、关系类型、Web 主视图、评测阈值或 DesignRef 变化时，同步
  正文、Mermaid、code-map 和设计语义测试。
- 已接受但未实现的能力使用稳定 `DES-NNN` 在设计符合度和 tracker 双向登记，文档不得标记为
  完全实现。

## 执行与门禁

1. 先在 code-map 定位模块职责、DesignRef、状态和定向测试，再修改代码或文档。
2. 职责或契约变化时先完成适用 ADR/设计，再同步实现、文件头、code-map 和语义测试。
3. `tests/test_code_document_mapping.py` 守护受管路径、文件头和双向引用；架构与设计语义测试分别
   从代码类型、AST、HTML 和常量验证当前契约。
4. 自动测试不能判断设计是否合理；计划必须声明人工审阅边界、当前符合度和未实现偏差。

## 变更与取代

改变 code-map 的事实源地位、受管范围、文件头必需字段、同步触发项或语义门禁时必须先新增 ADR。
模块清单和 DesignRef 的普通增删属于实现同步，不单独建立治理 ADR。旧映射由 Git 历史恢复，
Legacy 依据只进入 History，不在 standards 保存版本副本。
