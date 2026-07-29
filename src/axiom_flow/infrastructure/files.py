"""
模块职责：在受控数据根目录内解析可下载的本地文件。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
关联测试：tests/test_v03_api.py
"""

from pathlib import Path

from axiom_flow.domain.models import FileResource, NotFoundError


class LocalFileLocator:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.resolve()

    def resolve(self, stored_path: str, media_type: str, filename: str | None = None) -> FileResource:
        candidate = Path(stored_path)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("文件路径超出数据目录")
        if not resolved.is_file():
            raise NotFoundError("文件不存在")
        return FileResource(resolved, media_type, filename or resolved.name)
