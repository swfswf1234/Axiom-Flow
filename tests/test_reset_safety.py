"""
模块职责：验证开发数据库重建必须使用安全目标和完整确认词。
设计关联（DesignRef）：docs/adr/0007-versioned-domain-records.md
实现状态：Current
被测代码：src/axiom_flow/tools/reset_dev_database.py
"""

import pytest

from axiom_flow.infrastructure.config import Settings
from axiom_flow.tools.reset_dev_database import reset


def test_reset_rejects_system_database_before_connecting():
    with pytest.raises(ValueError, match="系统数据库"):
        reset("mysql", True, "RESET mysql", Settings())


def test_reset_requires_exact_confirmation(mysql_settings: Settings):
    with pytest.raises(ValueError, match="确认词不匹配"):
        reset(mysql_settings.mysql_test_database, False, "yes", mysql_settings)
