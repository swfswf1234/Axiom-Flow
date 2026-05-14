import json
import re
from pathlib import Path
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class FormulaBlock:
    tex: str
    bbox: dict
    page_no: int


@dataclass
class ParseResult:
    markdown: str
    pages: list[dict] = field(default_factory=list)
    doc_id: str = ""


MINERU_TYPE_MAP = {
    "title": "heading",
    "paragraph": "text",
    "equation_interline": "formula",
    "equation_inline": "formula",
    "image": "figure",
    "table": "table",
    "page_header": "text",
    "page_footer": "text",
    "page_number": "text",
    "page_footnote": "text",
    "list": "text",
    "algorithm": "text",
    "code": "text",
    "index": "text",
    "chart": "figure",
}


def _extract_text(content: dict) -> str:
    if isinstance(content, str):
        return content
    for key in ("paragraph_content", "title_content", "list_content", "text"):
        val = content.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict):
                    parts.append(item.get("content", "") or _extract_text(item))
                else:
                    parts.append(str(item))
            return "".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _extract_latex(content: dict) -> str | None:
    for key in ("latex", "math_content", "math"):
        val = content.get(key)
        if val and isinstance(val, str):
            return val
    return None


def _bbox_to_dict(bbox) -> dict:
    if isinstance(bbox, dict):
        return bbox
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return {"x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]}
    return {}


class MinerUService:
    def __init__(self, output_dir: str | Path = "data/parsed"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def parse_pdf(self, pdf_path: str, lang: str = "ch") -> ParseResult:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        book_name = pdf_path.stem
        out_dir = self.output_dir / book_name
        out_dir.mkdir(parents=True, exist_ok=True)

        pdf_bytes = pdf_path.read_bytes()
        from mineru.cli.common import do_parse as mineru_parse

        mineru_parse(
            output_dir=str(out_dir),
            pdf_file_names=[pdf_path.name],
            pdf_bytes_list=[pdf_bytes],
            p_lang_list=[lang],
            backend="pipeline",
            formula_enable=True,
            table_enable=True,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            f_dump_md=True,
            f_dump_middle_json=True,
            f_dump_model_output=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=True,
        )
        return self._collect_output(out_dir, pdf_path.name)

    def parse_pdf_batch(self, pdf_paths: list[str]) -> list[ParseResult]:
        results = []
        for path in pdf_paths:
            try:
                results.append(self.parse_pdf(path))
            except Exception as e:
                logger.error(f"Failed to parse {path}: {e}")
        return results

    def _collect_output(self, out_dir: Path, pdf_name: str) -> ParseResult:
        stem = Path(pdf_name).stem

        md_path = next(out_dir.rglob("*.md"), None)
        md_content = md_path.read_text(encoding="utf-8") if md_path else ""

        cl_path = next(out_dir.rglob("*_content_list_v2.json"), None)
        raw_pages = json.loads(cl_path.read_text(encoding="utf-8")) if cl_path else []

        pages = self._extract_pages(raw_pages)
        return ParseResult(markdown=md_content, pages=pages)

    def _extract_pages(self, raw_pages: list) -> list[dict]:
        if not isinstance(raw_pages, list):
            return []
        result = []
        for page_idx, blocks in enumerate(raw_pages):
            if not isinstance(blocks, list):
                continue
            page_blocks = []
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                mtype = blk.get("type", "paragraph")
                content = blk.get("content", {})
                page_blocks.append({
                    "type": MINERU_TYPE_MAP.get(mtype, "text"),
                    "mineru_type": mtype,
                    "content": _extract_text(content),
                    "latex": _extract_latex(content),
                    "bbox": _bbox_to_dict(blk.get("bbox", [])),
                    "confidence": blk.get("confidence", 1.0),
                })
            result.append({"page_no": page_idx + 1, "blocks": page_blocks})
        return result

    def extract_formulas(self, markdown: str) -> list[FormulaBlock]:
        formulas = []
        pattern = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
        for match in pattern.finditer(markdown):
            formulas.append(FormulaBlock(
                tex=match.group(1).strip(),
                bbox={},
                page_no=0,
            ))
        return formulas
