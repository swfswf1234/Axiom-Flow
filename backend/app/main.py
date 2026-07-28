"""
模块职责：保留稳定的 Uvicorn 导入入口，并转发到 v0.3 API 组装模块。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_v03_api.py
"""

from backend.api.main import app, create_app

__all__ = ["app", "create_app"]
