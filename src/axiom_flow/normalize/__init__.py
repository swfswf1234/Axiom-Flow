"""后处理归一化：引擎原始输出 → 统一 blocks / Markdown / 质量信号 / 分块。

设计事实源：`docs/design/parsing-pipeline.md`。
- `blocks`：引擎输出 → 统一 blocks（类型映射、bbox 缩放、阅读序）与页级 Markdown；
- `chunking`：书级 blocks → chunks（`chunks.json`，派生可重建）；
- `intents`：读时把人工意图（块类型改判）叠加到产物 blocks，不写回产物。

质量信号（`compute_quality`）与页级归一化（`normalize_page`）在 `blocks` 模块内提供。
"""

from axiom_flow.normalize.blocks import (
    blocks_to_markdown,
    compute_quality,
    normalize_page,
    scale_bbox,
)

__all__ = ["blocks_to_markdown", "compute_quality", "normalize_page", "scale_bbox"]
