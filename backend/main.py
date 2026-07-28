"""
模块职责：提供 Axiom-Flow 唯一 ASGI 应用入口。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_v03_api.py
"""

from backend.api.main import create_app

app = create_app()
