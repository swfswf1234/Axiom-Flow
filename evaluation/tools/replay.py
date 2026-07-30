"""
模块职责：使用公开 fixture 的冻结页面响应和生产产物写入器生成确定性解析包。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/system/test_public_fixture_regression.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import fitz

from axiom_flow.infrastructure.artifacts import ArtifactFile, ParseArtifactWriter


def materialize_fixture(fixture_dir: Path, data_dir: Path) -> Path:
    """把冻结 replay 转成与生产解析一致的 schema v2 产物包。"""
    fixture_dir = fixture_dir.resolve()
    fixture = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
    source = fixture_dir / str(fixture["source"]["path"])
    source_hash = _sha256(source)
    if source_hash != fixture["source"]["sha256"]:
        raise ValueError("fixture 源 PDF 哈希不一致")

    replay_dir = fixture_dir / "replay" / "pages"
    page_payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(replay_dir.glob("page-*.json"))
    ]
    if len(page_payloads) != int(fixture["source"]["page_count"]):
        raise ValueError("fixture replay 页数与声明不一致")

    writer = ParseArtifactWriter(data_dir, source_hash, str(fixture["replay"]["run_id"]))
    shared_assets: list[ArtifactFile] = []
    pages: list[dict[str, Any]] = []
    with fitz.open(source) as document:
        if len(document) != len(page_payloads):
            raise ValueError("fixture PDF 页数与 replay 不一致")
        for expected_no, replay in enumerate(page_payloads, 1):
            page_no = int(replay["page_no"])
            if page_no != expected_no:
                raise ValueError("fixture replay 页码必须连续且从 1 开始")
            page = document[page_no - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False)
            image = writer.write_page_image(page_no, pixmap.tobytes("png"))
            shared_assets.append(image)

            content_images = []
            blocks = replay.get("blocks", [])
            for block in blocks:
                if block.get("kind") not in {"figure", "table"} or not block.get("bbox"):
                    continue
                clip = fitz.Rect(block["bbox"]) & page.rect
                if clip.is_empty:
                    raise ValueError(f"第 {page_no} 页内容图片 bbox 无效")
                crop = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), clip=clip, alpha=False)
                artifact = writer.write_content_image(
                    page_no,
                    int(block["order_no"]),
                    str(block["kind"]),
                    list(block["bbox"]),
                    crop.tobytes("png"),
                )
                content_images.append(writer.relative_path(artifact.path))

            markdown = str(replay["markdown"])
            page_payload = {
                "schema_version": 1,
                "page_no": page_no,
                "page_kind": str(replay.get("page_kind", "content")),
                "markdown": markdown,
                "blocks": blocks,
                "evidence": replay.get("evidence", []),
                "quality": replay.get("quality", {}),
                "page_image": writer.relative_path(image.path),
                "content_images": content_images,
            }
            provider_payload = {
                "fixture_id": fixture["fixture_id"],
                "page_no": page_no,
                "page_kind": page_payload["page_kind"],
                "markdown": markdown,
                "blocks": blocks,
            }
            writer.write_page_payloads(page_no, markdown, page_payload, provider_payload)
            pages.append({"page_no": page_no, "markdown": markdown})

    writer.finalize(pages, dict(fixture["replay"]["provider"]), shared_assets)
    writer.verify_manifest()
    return writer.run_dir


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-data-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = materialize_fixture(args.fixture, args.output_data_dir)
    print(run_dir)


if __name__ == "__main__":
    main()
