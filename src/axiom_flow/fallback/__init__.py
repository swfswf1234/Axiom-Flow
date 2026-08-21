"""fallback：qwen-vl-plus 兜底通道（页图 → Markdown → 统一 blocks）。

设计关联（DesignRef）：docs/design/parsing-pipeline.md
实现状态：Current（V2-010 实验落地，V2-006 继承）
关联测试：tests/unit/test_fallback.py
"""

from axiom_flow.fallback.client import QwenVLPlusClient, VisionError, VisionResult
from axiom_flow.fallback.normalize import markdown_to_blocks

__all__ = ["QwenVLPlusClient", "VisionError", "VisionResult", "markdown_to_blocks"]
