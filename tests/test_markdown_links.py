"""
模块职责：验证仓库 Markdown 文档中的本地相对链接不会失效。
设计关联（DesignRef）：docs/standards/code-document-traceability.md
实现状态：Current
被测代码：README.md、docs、evaluation
"""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^]]*]\((<[^>]+>|[^)\s]+)(?:\s+['\"][^)]*['\"])?\)")


def _markdown_files() -> list[Path]:
    files = [ROOT / "README.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "evaluation").rglob("*.md"))
    return [path for path in files if path.is_file()]


def test_local_markdown_links_resolve():
    missing = []
    for document in _markdown_files():
        for match in LINK.finditer(document.read_text(encoding="utf-8")):
            raw = match.group(1).strip("<>")
            if raw.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = unquote(raw.split("#", 1)[0].split("?", 1)[0])
            if target and not (document.parent / target).resolve().exists():
                missing.append(f"{document.relative_to(ROOT).as_posix()}: {raw}")
    assert not missing, "失效的 Markdown 链接：\n" + "\n".join(missing)
