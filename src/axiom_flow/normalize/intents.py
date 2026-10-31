"""人工意图回放：把 edit 行的类型改判覆盖到产物块序列（读时、零写库、零重解析）。

设计事实源：`docs/design/parsing-management.md`（人工意图与产物的分职）与
`docs/design/knowledge-analysis.md`（`KA-2` 取证面）。产物文件是解析版本的自证，人工结论只落
`af_operations` 的意图行，读取时叠加——所以「改判后磁盘上还是原判」是设计内状态，不是缺陷。

只回放**判别维**：`type` 被改判覆盖，`text`／`latex`／`level` 等内容字段一律不补、不动
（判成 `formula` 而产物没有 latex，读出来就是 `type="formula"` 且 `latex` 为 `None`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from axiom_flow.schemas import Block, BlockType

#: 闭集查表：`BlockType` 是唯一来源，不另写一份名字表（改判集与块模型同源）。
_BLOCK_TYPES = {member.value: member for member in BlockType}


class RelabelIntent(Protocol):
    """回放只读这两件事：改判落在哪个块下标、判成了什么类型。

    `EditRow` 是它的现成实现者；用 Protocol 而不是直接收 `EditRow`，是为了让本模块不依赖 `db`
    （后处理层只吃数据形状，不认识仓储）。
    """

    block_index: int
    corrected_type: str | None


def relabels_by_block(intents: Sequence[RelabelIntent]) -> dict[int, BlockType]:
    """意图序列 → `{块下标: 改判类型}`：没判的行与闭集外的取值都不进表。

    闭集外的取值只可能来自库里的脏行（端点侧的 422 已经拦过一轮）。这里跳过而不是抛错：
    一行脏数据不该让整页读不出来——对照视图是人工核对的入口，拒绝读取比拒绝该行更糟。
    """
    relabels: dict[int, BlockType] = {}
    for intent in intents:
        block_type = _BLOCK_TYPES.get(intent.corrected_type or "")
        if block_type is None:
            continue
        relabels[int(intent.block_index)] = block_type
    return relabels


def apply_relabels(blocks: Sequence[Block], relabels: dict[int, BlockType]) -> list[Block]:
    """按块下标覆盖 `type`，其余字段逐字保留（改判不改内容，见模块 docstring）。

    匹配用 `Block.index` 而不是数组位置——它是块编辑的定位坐标（`schemas.py` 的 `index` 字段
    就这么定义的），产物数组万一将来被重排，改判仍落在同一个块上。
    """
    if not relabels:
        return list(blocks)
    return [
        block.model_copy(update={"type": relabels[block.index]})
        if block.index in relabels
        else block
        for block in blocks
    ]
