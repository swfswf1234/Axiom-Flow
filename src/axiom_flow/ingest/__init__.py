"""ingest：PDF 导入、页图渲染与 book.json 生成（spec §组件职责）。

设计关联（DesignRef）：docs/design/parsing-pipeline.md
实现状态：Current
关联测试：tests/unit/test_ingest.py
"""

from axiom_flow.ingest.importer import default_data_dir, import_book

__all__ = ["default_data_dir", "import_book"]
