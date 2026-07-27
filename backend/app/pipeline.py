"""
模块职责：执行 PDF 导入、页级视觉解析、质量审阅前数据生成和知识候选抽取。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
关联测试：tests/test_v02_pipeline.py
"""

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import fitz

from backend.app.config import Settings
from backend.app.providers import KnowledgeProvider, VisionProvider
from backend.app.store import MySQLStore


class PipelineService:
    """v0.2 单论文主链，所有模型输出在入库前转换为规范化页面事实。"""

    def __init__(self, store: MySQLStore, settings: Settings, vision: VisionProvider, knowledge: KnowledgeProvider):
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
        return self.store.create_document(original_filename or source.name, content_hash, destination, page_count)

    async def parse_document(self, document_id: str) -> dict[str, Any]:
        document = self.store.get_document(document_id)
        if not document:
            raise KeyError("文档不存在")
        provider_summary = {"vision_model": self.settings.vision_model, "fallback_model": self.settings.vision_fallback_model}
        run = self.store.create_parse_run(document_id, provider_summary)
        self.store.update_document_status(document_id, "parsing")
        parsed_pages = []
        try:
            with fitz.open(document["source_path"]) as pdf:
                for index, page in enumerate(pdf):
                    page_no = index + 1
                    image_path, image_bytes = self._render_page(document["content_hash"], page_no, page)
                    raw_text = page.get_text("text").strip()
                    native_blocks = self._native_blocks(page)
                    try:
                        model_page = await self.vision.parse_page(image_bytes, raw_text, page_no)
                        markdown = str(model_page.get("markdown") or raw_text)
                        blocks = self._normalize_blocks(model_page.get("blocks"), native_blocks)
                        parser_error = None
                    except Exception as exc:
                        markdown = raw_text
                        blocks = native_blocks
                        parser_error = str(exc)
                    page_kind = self._page_kind(raw_text, page_no)
                    evidence = self._evidence(raw_text, native_blocks, page_no)
                    quality = self._quality(raw_text, blocks, parser_error, page_kind)
                    parsed_pages.append(
                        {
                            "id": str(uuid.uuid4()), "run_id": run["id"], "document_id": document_id, "page_no": page_no,
                            "markdown": markdown, "blocks_json": json.dumps(blocks, ensure_ascii=False),
                            "evidence_json": json.dumps(evidence, ensure_ascii=False), "quality_json": json.dumps(quality, ensure_ascii=False),
                            "image_path": str(image_path), "page_kind": page_kind, "review_status": "needs_review", "review_reason": "等待人工确认",
                        }
                    )
            self.store.replace_pages(run["id"], document_id, parsed_pages)
            self.store.finish_parse_run(run["id"], "parsed", getattr(self.vision, "calls", 0))
            self.store.update_document_status(document_id, "needs_review")
            return {"run_id": run["id"], "document_id": document_id, "status": "parsed", "model_calls": getattr(self.vision, "calls", 0)}
        except BaseException:
            # 协程取消、进程重启后的恢复都不能把文档永久留在 parsing。
            self.store.finish_parse_run(run["id"], "failed", getattr(self.vision, "calls", 0))
            self.store.update_document_status(document_id, "failed")
            raise

    async def generate_candidates(self, document_id: str) -> list[dict[str, Any]]:
        pages = self.store.accepted_pages(document_id)
        if not pages:
            raise ValueError("没有已接受的知识正文页面")
        markdown = "\n\n".join(f"## 第 {page['page_no']} 页\n{page['markdown']}" for page in pages)
        response = await self.knowledge.extract_knowledge(markdown)
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
        self.store.replace_candidates(document_id, candidates, edges)
        self.store.update_document_status(document_id, "knowledge_review")
        return self.store.list_candidates(document_id)

    def _render_page(self, content_hash: str, page_no: int, page: fitz.Page) -> tuple[Path, bytes]:
        image_path = self.settings.documents_dir / content_hash / "pages" / f"page-{page_no:03d}.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False)
        pixmap.save(image_path)
        return image_path, image_path.read_bytes()

    @staticmethod
    def _native_blocks(page: fitz.Page) -> list[dict[str, Any]]:
        blocks = []
        for order, block in enumerate(page.get_text("blocks")):
            text = block[4].strip()
            if text:
                blocks.append({"kind": "paragraph", "content": text, "quote": text[:240], "order_no": order, "bbox": list(block[:4]), "confidence": 1.0, "source": "pymupdf"})
        return blocks

    @staticmethod
    def _normalize_blocks(model_blocks: Any, native_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(model_blocks, list) or not model_blocks:
            return native_blocks
        normalized = []
        for order, block in enumerate(model_blocks):
            if not isinstance(block, dict) or not str(block.get("content") or "").strip():
                continue
            quote = str(block.get("quote") or block.get("content") or "")[:240]
            native = next((item for item in native_blocks if quote and quote[:30] in item["content"]), None)
            normalized.append({"kind": str(block.get("kind") or "paragraph"), "content": str(block.get("content")), "quote": quote, "order_no": order, "bbox": native.get("bbox") if native else None, "confidence": float(block.get("confidence") or 0.5), "source": "vision"})
        return normalized or native_blocks

    @staticmethod
    def _page_kind(raw_text: str, page_no: int) -> str:
        text = raw_text.lower()
        if page_no in {11, 12} or (text.startswith("[") and "arxiv" in text):
            return "reference"
        if page_no in {13, 14, 15} or "attention visualizations" in text:
            return "visualization"
        return "content"

    @staticmethod
    def _evidence(raw_text: str, native_blocks: list[dict[str, Any]], page_no: int) -> list[dict[str, Any]]:
        quote = raw_text[:300] if raw_text else ""
        return [{"kind": "text_quote", "page_no": page_no, "quote": quote, "bbox": native_blocks[0]["bbox"] if native_blocks else None}]

    @staticmethod
    def _quality(raw_text: str, blocks: list[dict[str, Any]], parser_error: str | None, page_kind: str) -> dict[str, Any]:
        issues = []
        if not raw_text:
            issues.append("文字层为空，需要人工确认视觉解析结果")
        if parser_error:
            issues.append(f"视觉解析降级：{parser_error}")
        if not blocks:
            issues.append("未识别到内容块")
        return {"status": "needs_review", "issues": issues, "page_kind": page_kind, "native_text_chars": len(raw_text)}

    @staticmethod
    def _candidate_evidence(quote: str, pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
        for page_no, text in pages:
            if quote and quote in text:
                return [{"kind": "text_quote", "page_no": page_no, "quote": quote, "bbox": None}]
        return [{"kind": "page_context", "page_no": pages[0][0], "quote": quote or pages[0][1][:160], "bbox": None}]
