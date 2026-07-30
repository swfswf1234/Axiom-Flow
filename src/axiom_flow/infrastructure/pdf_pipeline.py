"""
模块职责：执行 PDF 导入、页级视觉解析、质量审阅前数据生成和知识候选抽取。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
关联测试：tests/system/test_document_release_flow.py、tests/integration/test_jobs.py
"""

import hashlib
import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fitz

from axiom_flow.application.ports import KnowledgeProvider, VisionProvider
from axiom_flow.infrastructure.artifacts import ArtifactFile, ParseArtifactWriter
from axiom_flow.infrastructure.bailian import InvalidModelPage
from axiom_flow.infrastructure.config import Settings


class PDFPipeline:
    """v0.2 单论文主链，所有模型输出在入库前转换为规范化页面事实。"""

    def __init__(self, store: Any, settings: Settings, vision: VisionProvider, knowledge: KnowledgeProvider):
        self.store = store
        self.settings = settings
        self.vision = vision
        self.knowledge = knowledge

    def import_pdf(self, source: Path, original_filename: str | None = None) -> dict[str, Any]:
        """复制 PDF 到内容哈希目录，保证后续解析可重复。"""
        payload = source.read_bytes()
        content_hash = hashlib.sha256(payload).hexdigest()
        destination_dir = self.settings.documents_dir / content_hash
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / "source.pdf"
        if not destination.exists():
            shutil.copyfile(source, destination)
        with fitz.open(destination) as pdf:
            page_count = len(pdf)
        find_by_hash = getattr(self.store, "get_document_by_hash", None)
        existing = find_by_hash(content_hash) if find_by_hash else None
        if existing:
            return existing
        document = self.store.create_document(original_filename or source.name, content_hash, destination, page_count)
        register = getattr(self.store, "register_artifact", None)
        if register:
            register(
                document["id"], None, "source_pdf", destination,
                mime_type="application/pdf", metadata={"original_filename": document["filename"]},
                data_root=self.settings.data_dir,
            )
        return document

    async def parse_document(
        self,
        document_id: str,
        job_id: str | None = None,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        page_start: int = 1,
        page_end: int | None = None,
    ) -> dict[str, Any]:
        document = self.store.get_document(document_id)
        if not document:
            raise KeyError("文档不存在")
        resolved_end = page_end if page_end is not None else int(document["page_count"])
        if page_start < 1 or resolved_end < page_start or resolved_end > int(document["page_count"]):
            raise ValueError("解析页范围超出文档边界")
        selected_count = resolved_end - page_start + 1
        provider_summary = {
            "adapter": type(self.vision).__name__, "vision_model": self.settings.vision_model,
            "contract_version": self.settings.vision_contract_version,
            "model_call_budget": self.settings.model_call_budget,
            "max_tokens": self.settings.vision_max_tokens,
            "max_attempts_per_page": self.settings.vision_page_attempts,
            "page_range": {"start": page_start, "end": resolved_end, "inclusive": True},
        }
        run = self.store.create_parse_run(document_id, provider_summary, job_id)
        persisted_calls = int(run.get("model_calls") or 0)
        if hasattr(self.vision, "calls") and self.vision.calls < persisted_calls:
            self.vision.calls = persisted_calls
        writer = ParseArtifactWriter(self.settings.data_dir, document["content_hash"], run["id"])
        self.store.update_document_status(document_id, "parsing")
        append_page = getattr(self.store, "append_page", None)
        list_run_pages = getattr(self.store, "list_pages_for_run", None)
        existing_pages = list_run_pages(run["id"]) if list_run_pages else []
        existing_by_no = {int(page["page_no"]): page for page in existing_pages}
        pending_pages = []
        shared_page_artifacts: dict[int, ArtifactFile] = {}
        try:
            prepare_document = getattr(self.vision, "prepare_document", None)
            if prepare_document:
                await prepare_document(Path(document["source_path"]), writer.run_dir)
            with fitz.open(document["source_path"]) as pdf:
                for completed, page_no in enumerate(range(page_start, resolved_end + 1), 1):
                    if cancelled and cancelled():
                        raise InterruptedError("任务已请求取消")
                    page = pdf[page_no - 1]
                    if page_no in existing_by_no:
                        if not writer.page_is_complete(page_no):
                            raise ValueError(f"第 {page_no} 页检查点文件不完整")
                        if progress:
                            progress(completed, selected_count)
                        continue
                    image_bytes, ink_ratio = self._render_page(page)
                    raw_text = page.get_text("text").strip()
                    native_blocks = self._native_blocks(page)
                    parser_error = None
                    try:
                        model_page = await self.vision.parse_page(image_bytes, raw_text, page_no)
                        if not isinstance(model_page, dict):
                            raise ValueError("解析器响应不是 JSON 对象")
                    except Exception as exc:
                        if isinstance(exc, InvalidModelPage):
                            failure = writer.write_provider_failure(
                                page_no, str(exc), exc.response_content, exc.metadata,
                            )
                            self._register_artifacts(document_id, run["id"], [failure])
                            checkpoint = getattr(self.store, "checkpoint_parse_run", None)
                            if checkpoint:
                                checkpoint(run["id"], int(getattr(self.vision, "calls", persisted_calls)))
                        if not raw_text:
                            raise
                        model_page = {"markdown": raw_text, "blocks": native_blocks, "page_kind": "content"}
                        parser_error = str(exc)[:1000]
                    page_kind = str(model_page.get("page_kind") or "content")
                    if page_kind not in {"content", "reference", "blank"}:
                        page_kind = "content"
                    markdown = str(model_page.get("markdown") or raw_text).strip()
                    if not markdown and page_kind != "blank":
                        if ink_ratio < 0.002:
                            page_kind = "blank"
                        else:
                            raise ValueError(f"第 {page_no} 页解析正文为空")
                    blocks = self._normalize_blocks(model_page.get("blocks"), native_blocks, page)
                    if not blocks and page_kind != "blank":
                        raise ValueError(f"第 {page_no} 页未识别到内容块")
                    evidence = self._evidence(raw_text, native_blocks, page_no, page)
                    quality = self._quality(raw_text, blocks, parser_error, page_kind, ink_ratio)
                    if isinstance(model_page.get("_provider"), dict):
                        quality["provider"] = model_page["_provider"]

                    image_artifact = writer.write_page_image(page_no, image_bytes)
                    shared_page_artifacts[page_no] = image_artifact
                    content_artifacts = self._write_content_images(writer, page, page_no, blocks)
                    page_payload = {
                        "schema_version": 1, "page_no": page_no, "page_kind": page_kind,
                        "markdown": markdown, "blocks": blocks, "evidence": evidence, "quality": quality,
                        "page_image": writer.relative_path(image_artifact.path),
                        "content_images": [writer.relative_path(item.path) for item in content_artifacts],
                    }
                    payload_artifacts = writer.write_page_payloads(page_no, markdown, page_payload, model_page)
                    page_row = {
                        "id": str(uuid.uuid4()), "run_id": run["id"], "document_id": document_id, "page_no": page_no,
                        "markdown": markdown, "blocks_json": json.dumps(blocks, ensure_ascii=False),
                        "evidence_json": json.dumps(evidence, ensure_ascii=False), "quality_json": json.dumps(quality, ensure_ascii=False),
                        "image_path": writer.relative_path(image_artifact.path), "page_kind": page_kind,
                        "review_status": "needs_review", "review_reason": "等待人工确认",
                    }
                    if append_page:
                        append_page(run["id"], document_id, page_row)
                    else:
                        pending_pages.append(page_row)
                    self._register_artifacts(document_id, None, [image_artifact])
                    self._register_artifacts(document_id, run["id"], [*content_artifacts, *payload_artifacts])
                    checkpoint = getattr(self.store, "checkpoint_parse_run", None)
                    if checkpoint:
                        checkpoint(run["id"], int(getattr(self.vision, "calls", persisted_calls)))
                    if progress:
                        progress(completed, selected_count)
            if pending_pages:
                self.store.replace_pages(run["id"], document_id, pending_pages)
            pages = list_run_pages(run["id"]) if list_run_pages else pending_pages
            if not shared_page_artifacts:
                for page in pages:
                    page_no = int(page["page_no"])
                    path = self.settings.data_dir / str(page["image_path"])
                    shared_page_artifacts[page_no] = writer.link_shared_page_image(page_no, path)
            final_artifacts = writer.finalize(pages, provider_summary, list(shared_page_artifacts.values()))
            writer.verify_manifest()
            self._register_artifacts(document_id, run["id"], final_artifacts)
            calls = int(getattr(self.vision, "calls", persisted_calls))
            self.store.finish_parse_run(run["id"], "parsed", calls)
            self.store.update_document_status(document_id, "needs_review")
            manifest = next(item for item in final_artifacts if item.kind == "parse_manifest")
            return {
                "run_id": run["id"], "document_id": document_id, "status": "parsed",
                "model_calls": calls, "manifest_hash": manifest.content_hash,
            }
        except BaseException as exc:
            # 协程取消、进程重启后的恢复都不能把文档永久留在 parsing。
            calls = int(getattr(self.vision, "calls", persisted_calls))
            status = "cancelled" if isinstance(exc, InterruptedError) else "failed"
            self.store.finish_parse_run(run["id"], status, calls, {"message": str(exc)[:1000]})
            self.store.update_document_status(document_id, "failed")
            raise

    async def generate_candidates(self, document_id: str, job_id: str | None = None) -> list[dict[str, Any]]:
        pages = self.store.accepted_pages(document_id)
        if not pages:
            raise ValueError("没有已接受的知识正文页面")
        markdown = "\n\n".join(f"## 第 {page['page_no']} 页\n{page['markdown']}" for page in pages)
        parse_run_id = str(pages[0]["run_id"])
        calls_before = getattr(self.knowledge, "calls", 0)
        create_extraction = getattr(self.store, "create_extraction_run", None)
        extraction = create_extraction(
            document_id, parse_run_id, job_id, {"knowledge_model": self.settings.knowledge_model}
        ) if create_extraction else None
        try:
            response = await self.knowledge.extract_knowledge(markdown)
        except Exception as exc:
            if extraction:
                self.store.finish_extraction_run(
                    extraction["id"], "failed", getattr(self.knowledge, "calls", 0) - calls_before,
                    {"message": str(exc)[:1000]},
                )
            raise
        page_by_quote = [(page["page_no"], page["markdown"]) for page in pages]
        candidates = []
        title_to_id = {}
        for item in response.get("nodes", []):
            title = str(item.get("title") or "").strip()
            if not title or title in title_to_id:
                continue
            evidence = self._candidate_evidence(str(item.get("evidence_quote") or ""), page_by_quote)
            candidate_id = str(uuid.uuid4())
            title_to_id[title] = candidate_id
            candidates.append({"id": candidate_id, "document_id": document_id, "kind": str(item.get("kind") or "concept"), "title": title, "content": str(item.get("content") or ""), "evidence_json": json.dumps(evidence, ensure_ascii=False), "review_status": "needs_review", "review_reason": "等待人工确认"})
        edges = []
        allowed_relations = {"CONTAINS", "PREREQUISITE_OF", "DEFINES", "PROVES", "USES", "ILLUSTRATES", "RELATED_TO"}
        for item in response.get("edges", []):
            source_id = title_to_id.get(str(item.get("source_title") or ""))
            target_id = title_to_id.get(str(item.get("target_title") or ""))
            if source_id and target_id:
                evidence = self._candidate_evidence(str(item.get("evidence_quote") or ""), page_by_quote)
                relation = str(item.get("relation") or "RELATED_TO")
                edges.append({"id": str(uuid.uuid4()), "document_id": document_id, "source_id": source_id, "target_id": target_id, "relation": relation if relation in allowed_relations else "RELATED_TO", "evidence_json": json.dumps(evidence, ensure_ascii=False), "review_status": "needs_review"})
        if extraction:
            self.store.append_candidates(extraction["id"], document_id, candidates, edges)
            self.store.finish_extraction_run(
                extraction["id"], "succeeded", getattr(self.knowledge, "calls", 0) - calls_before,
            )
        else:
            self.store.replace_candidates(document_id, candidates, edges)
        self.store.update_document_status(document_id, "knowledge_review")
        return self.store.list_candidates(document_id)

    def _render_page(self, page: fitz.Page) -> tuple[bytes, float]:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False)
        samples = pixmap.samples
        channels = pixmap.n
        dark = sum(1 for offset in range(0, len(samples), channels) if min(samples[offset:offset + 3]) < 245)
        ink_ratio = dark / max(1, pixmap.width * pixmap.height)
        return pixmap.tobytes("png"), ink_ratio

    @staticmethod
    def _native_blocks(page: fitz.Page) -> list[dict[str, Any]]:
        blocks = []
        for order, block in enumerate(page.get_text("blocks")):
            text = block[4].strip()
            if text:
                blocks.append({"kind": "paragraph", "content": text, "quote": text[:240], "order_no": order, "bbox": list(block[:4]), "confidence": 1.0, "source": "pymupdf"})
        return blocks

    @staticmethod
    def _normalize_blocks(
        model_blocks: Any, native_blocks: list[dict[str, Any]], page: fitz.Page,
    ) -> list[dict[str, Any]]:
        if not isinstance(model_blocks, list) or not model_blocks:
            return native_blocks
        normalized = []
        for order, block in enumerate(model_blocks):
            if not isinstance(block, dict) or not str(block.get("content") or "").strip():
                continue
            quote = str(block.get("quote") or block.get("content") or "")[:240]
            native = next((item for item in native_blocks if quote and quote[:30] in item["content"]), None)
            bbox = native.get("bbox") if native else PDFPipeline._normalized_bbox(block.get("bbox_1000"), page)
            normalized.append({
                "kind": str(block.get("kind") or "paragraph"), "content": str(block.get("content")),
                "latex": str(block.get("latex")) if block.get("latex") else None,
                "quote": quote, "order_no": order, "bbox": bbox,
                "confidence": float(block.get("confidence") or 0.5),
                "source": str(block.get("source") or "vision"),
            })
        return normalized or native_blocks

    @staticmethod
    def _evidence(
        raw_text: str, native_blocks: list[dict[str, Any]], page_no: int, page: fitz.Page,
    ) -> list[dict[str, Any]]:
        quote = raw_text[:300] if raw_text else ""
        if quote:
            return [{"kind": "text_quote", "page_no": page_no, "quote": quote, "bbox": native_blocks[0]["bbox"] if native_blocks else None}]
        return [{"kind": "page_image", "page_no": page_no, "quote": "", "bbox": [0.0, 0.0, page.rect.width, page.rect.height]}]

    @staticmethod
    def _quality(
        raw_text: str, blocks: list[dict[str, Any]], parser_error: str | None,
        page_kind: str, ink_ratio: float,
    ) -> dict[str, Any]:
        issues = []
        if not raw_text and page_kind != "blank":
            issues.append("扫描页无文字层，正文来自视觉解析")
        if parser_error:
            issues.append(f"视觉解析降级：{parser_error}")
        if not blocks:
            issues.append("未识别到内容块")
        return {
            "status": "needs_review", "issues": issues, "page_kind": page_kind,
            "native_text_chars": len(raw_text), "ink_ratio": round(ink_ratio, 6),
        }

    @staticmethod
    def _normalized_bbox(value: Any, page: fitz.Page) -> list[float] | None:
        if not isinstance(value, list) or len(value) != 4:
            return None
        try:
            x0, y0, x1, y1 = (float(item) for item in value)
        except (TypeError, ValueError):
            return None
        if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
            return None
        return [
            x0 * page.rect.width / 1000, y0 * page.rect.height / 1000,
            x1 * page.rect.width / 1000, y1 * page.rect.height / 1000,
        ]

    @staticmethod
    def _write_content_images(
        writer: ParseArtifactWriter, page: fitz.Page, page_no: int, blocks: list[dict[str, Any]],
    ) -> list[ArtifactFile]:
        artifacts = []
        for block in blocks:
            if block.get("kind") not in {"figure", "table"} or not block.get("bbox"):
                continue
            clip = fitz.Rect(block["bbox"]) & page.rect
            if clip.is_empty or clip.width < 1 or clip.height < 1:
                continue
            pixmap = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), clip=clip, alpha=False)
            artifacts.append(writer.write_content_image(
                page_no, int(block["order_no"]), str(block["kind"]), list(block["bbox"]), pixmap.tobytes("png"),
            ))
        return artifacts

    def _register_artifacts(self, document_id: str, run_id: str | None, artifacts: list[ArtifactFile]) -> None:
        register = getattr(self.store, "register_artifact_file", None)
        if register:
            for artifact in artifacts:
                register(document_id, run_id, artifact, self.settings.data_dir)

    @staticmethod
    def _candidate_evidence(quote: str, pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
        for page_no, text in pages:
            if quote and quote in text:
                return [{"kind": "text_quote", "page_no": page_no, "quote": quote, "bbox": None}]
        return [{"kind": "page_context", "page_no": pages[0][0], "quote": quote or pages[0][1][:160], "bbox": None}]
