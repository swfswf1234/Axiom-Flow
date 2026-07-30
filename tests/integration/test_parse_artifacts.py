"""
模块职责：验证解析运行产物包的固定目录、清单和完整性检查。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
被测代码：src/axiom_flow/infrastructure/artifacts.py
"""

import json

import pytest

from axiom_flow.infrastructure.artifacts import ParseArtifactWriter


def test_parse_artifact_bundle_is_content_addressed_and_verifiable(tmp_path):
    writer = ParseArtifactWriter(tmp_path / "data", "abc123", "run-1")
    image = writer.write_page_image(1, b"png-payload")
    writer.write_page_payloads(
        1, "正文", {"page_no": 1, "markdown": "正文"}, {"markdown": "正文", "blocks": []},
    )
    failure = writer.write_provider_failure(
        2, "invalid json", '{"markdown":"broken', {"finish_reason": "stop"},
    )

    artifacts = writer.finalize(
        [{"page_no": 1, "markdown": "正文"}],
        {"adapter": "fake", "page_range": {"start": 1, "end": 1, "inclusive": True}},
        [image],
    )
    manifest = writer.verify_manifest()

    assert writer.relative_path(image.path) == "documents/abc123/page-assets/render-200dpi-v1/page-0001.png"
    assert writer.page_is_complete(1, image.content_hash)
    assert manifest["page_count"] == 1
    assert manifest["page_numbers"] == [1]
    assert manifest["provider"]["page_range"]["end"] == 1
    assert manifest["schema_version"] == 2
    assert manifest["shared_assets"][0]["sha256"] == image.content_hash
    assert failure.kind == "provider_failure"
    assert {artifact.kind for artifact in artifacts} == {"document_markdown", "parse_manifest"}
    assert all("sha256" in item for item in manifest["files"])

    page_json = writer.run_dir / "pages" / "page-0001.json"
    page_json.write_text(json.dumps({"page_no": 2}), encoding="utf-8")
    assert writer.page_is_complete(1) is False
    with pytest.raises(ValueError, match="哈希不一致"):
        writer.verify_manifest()


def test_shared_page_images_are_reused_and_v1_manifest_remains_verifiable(tmp_path):
    data_root = tmp_path / "data"
    first = ParseArtifactWriter(data_root, "abc123", "run-1")
    first_image = first.write_page_image(1, b"stable-image")
    second = ParseArtifactWriter(data_root, "abc123", "run-2")
    second_image = second.write_page_image(1, b"stable-image")
    assert first_image.path == second_image.path
    assert first_image.content_hash == second_image.content_hash
    with pytest.raises(ValueError, match="不一致"):
        second.write_page_image(1, b"different-image")

    legacy_source = first.run_dir / "page-images" / "page-0002.png"
    legacy_source.parent.mkdir(parents=True)
    legacy_source.write_bytes(b"legacy-page")
    linked = first.link_shared_page_image(2, legacy_source)
    assert linked.path.read_bytes() == b"legacy-page"

    legacy = ParseArtifactWriter(data_root, "legacy", "run-v1")
    legacy.run_dir.mkdir(parents=True)
    payload = legacy.run_dir / "document.md"
    payload.write_text("legacy\n", encoding="utf-8")
    manifest = {
        "schema_version": 1, "run_id": "run-v1", "document_hash": "legacy",
        "page_count": 0, "page_numbers": [], "provider": {},
        "files": [{"path": "document.md", "sha256": legacy._sha256(payload), "size_bytes": payload.stat().st_size}],
    }
    (legacy.run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert legacy.verify_manifest()["schema_version"] == 1
