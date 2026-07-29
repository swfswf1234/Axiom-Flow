"""
模块职责：写入、校验并索引内容寻址的解析运行产物包。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
关联测试：tests/test_parse_artifacts.py
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """可登记到运行事实源的本地文件。"""

    kind: str
    path: Path
    content_hash: str
    size_bytes: int
    mime_type: str
    metadata: dict[str, Any]


class ParseArtifactWriter:
    """保证解析产物只落入指定文档和运行目录。"""

    def __init__(self, data_dir: Path, document_hash: str, run_id: str) -> None:
        self.data_dir = data_dir.resolve()
        self.document_dir = self.data_dir / "documents" / document_hash
        self.run_dir = self.document_dir / "parse-runs" / run_id
        self.page_asset_dir = self.document_dir / "page-assets" / "render-200dpi-v1"

    def relative_path(self, path: Path) -> str:
        """返回相对于数据根的稳定路径并拒绝目录逃逸。"""
        resolved = path.resolve()
        if not resolved.is_relative_to(self.data_dir):
            raise ValueError("解析产物路径超出数据目录")
        return resolved.relative_to(self.data_dir).as_posix()

    def write_page_image(self, page_no: int, payload: bytes) -> ArtifactFile:
        path = self.page_asset_dir / f"page-{page_no:04d}.png"
        if path.is_file():
            if self._sha256_bytes(payload) != self._sha256(path):
                raise ValueError(f"共享页图与既有渲染不一致：第 {page_no} 页")
        else:
            self._write_bytes(path, payload)
        return self._artifact(
            "page_image", path, {"page_no": page_no, "render_contract": "render-200dpi-v1"}, "image/png",
        )

    def link_shared_page_image(self, page_no: int, source: Path) -> ArtifactFile:
        """从既有不可变运行建立共享页图，优先使用 NTFS 硬链接。"""
        source = source.resolve()
        if not source.is_file() or not source.is_relative_to(self.data_dir):
            raise ValueError("共享页图来源无效或超出数据目录")
        target = self.page_asset_dir / f"page-{page_no:04d}.png"
        if target.is_file():
            if self._sha256(target) != self._sha256(source):
                raise ValueError(f"共享页图与来源哈希不一致：第 {page_no} 页")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        return self._artifact(
            "page_image", target, {"page_no": page_no, "render_contract": "render-200dpi-v1"}, "image/png",
        )

    def write_content_image(
        self, page_no: int, order_no: int, kind: str, bbox: list[float], payload: bytes,
    ) -> ArtifactFile:
        path = self.run_dir / "content-images" / f"page-{page_no:04d}-block-{order_no:04d}.png"
        self._write_bytes(path, payload)
        return self._artifact(
            "content_image", path,
            {"page_no": page_no, "order_no": order_no, "block_kind": kind, "bbox": bbox},
            "image/png",
        )

    def write_page_payloads(
        self, page_no: int, markdown: str, payload: dict[str, Any], provider_payload: dict[str, Any],
    ) -> list[ArtifactFile]:
        markdown_path = self.run_dir / "pages" / f"page-{page_no:04d}.md"
        json_path = self.run_dir / "pages" / f"page-{page_no:04d}.json"
        provider_path = self.run_dir / "provider" / f"page-{page_no:04d}.json"
        self._write_text(markdown_path, markdown + ("\n" if markdown and not markdown.endswith("\n") else ""))
        self._write_json(json_path, payload)
        self._write_json(provider_path, provider_payload)
        metadata = {"page_no": page_no}
        return [
            self._artifact("page_markdown", markdown_path, metadata, "text/markdown"),
            self._artifact("page_json", json_path, metadata, "application/json"),
            self._artifact("provider_response", provider_path, metadata, "application/json"),
        ]

    def write_provider_failure(
        self, page_no: int, error: str, response_content: str, metadata: dict[str, Any],
    ) -> ArtifactFile:
        """保存不含凭证的失败响应，供重试诊断而不把它登记为成功页。"""
        path = self.run_dir / "provider" / f"page-{page_no:04d}.failed.json"
        self._write_json(path, {
            "page_no": page_no, "error": error[:1000],
            "metadata": metadata, "content": response_content,
        })
        return self._artifact("provider_failure", path, {"page_no": page_no}, "application/json")

    def page_is_complete(self, page_no: int, expected_image_hash: str | None = None) -> bool:
        """检查断点页的三个基础产物，防止跳过损坏文件。"""
        image = self.page_asset_dir / f"page-{page_no:04d}.png"
        if not image.is_file():
            image = self.run_dir / "page-images" / f"page-{page_no:04d}.png"
        markdown = self.run_dir / "pages" / f"page-{page_no:04d}.md"
        page_json = self.run_dir / "pages" / f"page-{page_no:04d}.json"
        if not all(path.is_file() for path in (image, markdown, page_json)):
            return False
        if expected_image_hash and self._sha256(image) != expected_image_hash:
            return False
        try:
            value = json.loads(page_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return value.get("page_no") == page_no

    def finalize(
        self,
        pages: list[dict[str, Any]],
        provider_summary: dict[str, Any],
        shared_assets: list[ArtifactFile] | None = None,
    ) -> list[ArtifactFile]:
        """写入整书 Markdown 和最终清单；清单自身不纳入其文件列表。"""
        document_markdown = "\n\n".join(
            f"## 第 {page['page_no']} 页\n\n{page['markdown']}" for page in pages
        )
        document_path = self.run_dir / "document.md"
        self._write_text(document_path, document_markdown + "\n")
        document_artifact = self._artifact("document_markdown", document_path, {}, "text/markdown")

        files = []
        for path in sorted(self.run_dir.rglob("*")):
            if path.is_file() and path.name not in {"manifest.json", "manifest.partial.json"}:
                files.append({
                    "path": path.relative_to(self.run_dir).as_posix(),
                    "sha256": self._sha256(path),
                    "size_bytes": path.stat().st_size,
                    "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                })
        manifest = {
            "schema_version": 2,
            "run_id": self.run_dir.name,
            "document_hash": self.run_dir.parents[1].name,
            "page_count": len(pages),
            "page_numbers": [int(page["page_no"]) for page in pages],
            "provider": provider_summary,
            "files": files,
            "shared_assets": [
                {
                    "kind": item.kind,
                    "path": self.relative_path(item.path),
                    "sha256": item.content_hash,
                    "size_bytes": item.size_bytes,
                    "mime_type": item.mime_type,
                    "metadata": item.metadata,
                }
                for item in sorted(shared_assets or [], key=lambda value: str(value.path))
            ],
        }
        partial_path = self.run_dir / "manifest.partial.json"
        final_path = self.run_dir / "manifest.json"
        self._write_json(partial_path, manifest)
        partial_path.replace(final_path)
        manifest_artifact = self._artifact("parse_manifest", final_path, {}, "application/json")
        return [document_artifact, manifest_artifact]

    def verify_manifest(self) -> dict[str, Any]:
        """复算最终清单中全部文件哈希，兼容运行内 v1 和共享资产 v2。"""
        path = self.run_dir / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        schema_version = int(manifest.get("schema_version", 1))
        if schema_version not in {1, 2}:
            raise ValueError(f"不支持的解析清单版本：{schema_version}")
        for item in manifest.get("files", []):
            candidate = (self.run_dir / item["path"]).resolve()
            if not candidate.is_relative_to(self.run_dir.resolve()) or not candidate.is_file():
                raise ValueError(f"解析产物缺失：{item['path']}")
            if self._sha256(candidate) != item["sha256"]:
                raise ValueError(f"解析产物哈希不一致：{item['path']}")
        for item in manifest.get("shared_assets", []):
            candidate = (self.data_dir / item["path"]).resolve()
            if not candidate.is_relative_to(self.data_dir) or not candidate.is_file():
                raise ValueError(f"共享解析资产缺失：{item['path']}")
            if self._sha256(candidate) != item["sha256"]:
                raise ValueError(f"共享解析资产哈希不一致：{item['path']}")
        return manifest

    def _artifact(
        self, kind: str, path: Path, metadata: dict[str, Any], mime_type: str | None = None,
    ) -> ArtifactFile:
        return ArtifactFile(
            kind=kind,
            path=path,
            content_hash=self._sha256(path),
            size_bytes=path.stat().st_size,
            mime_type=mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            metadata=metadata,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sha256_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _write_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)

    @classmethod
    def _write_text(cls, path: Path, payload: str) -> None:
        cls._write_bytes(path, payload.encode("utf-8"))

    @classmethod
    def _write_json(cls, path: Path, payload: dict[str, Any]) -> None:
        cls._write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
