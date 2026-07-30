"""
模块职责：提供 ``python -m axiom_flow.worker`` 命令入口。
设计关联（DesignRef）：docs/design/background-jobs.md
实现状态：Current
关联测试：tests/integration/test_jobs.py
"""

import logging

from axiom_flow.worker.runner import build_worker

logging.basicConfig(level=logging.INFO)
build_worker().run_forever()
