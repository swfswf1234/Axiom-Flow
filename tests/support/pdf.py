"""
模块职责：为确定性测试生成最小文本 PDF。
设计关联（DesignRef）：docs/standards/testing.md
实现状态：Current
关联测试：tests/integration/test_api.py、tests/system/test_document_release_flow.py
"""

from collections.abc import Iterable
from pathlib import Path

import fitz


def write_text_pdf(path: Path, page_texts: Iterable[str]) -> None:
    """每个字符串生成一个带文字层的物理页。"""
    document = fitz.open()
    try:
        for text in page_texts:
            page = document.new_page()
            page.insert_text((72, 72), text)
        document.save(path)
    finally:
        document.close()
