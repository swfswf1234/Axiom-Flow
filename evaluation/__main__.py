"""
模块职责：把 `python -m evaluation` 转发到文档中心评测 CLI。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/unit/test_evaluation_cli.py
"""

from evaluation.cli import main

if __name__ == "__main__":
    main()
