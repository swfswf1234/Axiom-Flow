# 文档与代码双向追溯规范

状态：Current  
维护位置：`docs/standards/code-document-traceability.md`
关联代码：`evaluation/scorecard.py`、受管模块文件头  
关联测试：`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0001-local-first-and-storage.md`

## 目的

让人工和 Agent 都能从代码定位设计依据，也能从设计定位实际落地代码与测试。映射的唯一
事实源是 `docs/architecture/code-map.md`；文件头的 `DesignRef` 只用于代码阅读时反查。

## 文件头格式

非豁免 Python 模块在首个 import 前使用模块 docstring：

```python
"""
模块职责：说明本模块承担的业务或技术责任。
设计关联（DesignRef）：docs/design/example.md
实现状态：Current
关联测试：tests/test_example.py
"""
```

测试模块额外使用 `被测代码：`。`Legacy` 模块的 DesignRef 必须指向 `docs/history/`，并
明确其不属于当前目标架构。`__init__.py`、纯常量和极短辅助文件可不写文件头，但不得承载
业务规则。

## 维护规则

1. 新增、移动、删除受管模块时，先更新 `code-map.md`，再写代码文件头。
2. 修改模块职责、接口或数据语义时，同步更新关联设计、测试和必要的 ADR。
3. 活跃架构/设计文档头部必须包含 `关联代码`、`关联测试`、`关联 ADR`；无实现时写
   `尚未实现`，不能指向无关旧代码。
4. `tests/test_code_document_mapping.py` 是受管代码和活跃设计变更的必过定向测试。
