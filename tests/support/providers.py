"""
模块职责：提供不访问外部模型的确定性解析与发布供应商替身。
设计关联（DesignRef）：docs/standards/testing.md
实现状态：Current
关联测试：tests/integration/test_api.py、tests/system/test_document_release_flow.py
"""


class UnusedProvider:
    """验证 API 进程不会执行模型任务。"""

    calls = 0

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        raise AssertionError("API 进程不得执行解析")

    async def extract_knowledge(self, markdown: str) -> dict:
        raise AssertionError("API 进程不得执行抽取")


class ParsingProvider:
    """返回输入文字层对应的最小页面事实。"""

    def __init__(self) -> None:
        self.calls = 0

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        self.calls += 1
        return {
            "markdown": raw_text,
            "page_kind": "content",
            "blocks": [{"kind": "paragraph", "content": raw_text, "confidence": 1.0}],
        }
    async def extract_knowledge(self, markdown: str) -> dict:
        self.calls += 1
        return {"nodes": [], "edges": []}


class ReleaseProvider(ParsingProvider):
    """为发布系统测试生成带证据的稳定知识图。"""

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        self.calls += 1
        return {
            "markdown": raw_text,
            "blocks": [{
                "kind": "paragraph",
                "content": raw_text,
                "quote": "Axiom",
                "confidence": 0.9,
            }],
        }

    async def extract_knowledge(self, markdown: str) -> dict:
        self.calls += 1
        return {
            "nodes": [
                {"kind": "concept", "title": "Axiom", "content": "可追溯的知识单元", "evidence_quote": "Axiom"},
                {"kind": "result", "title": "Flow", "content": "由页面事实生成", "evidence_quote": "Flow"},
            ],
            "edges": [{
                "source_title": "Axiom",
                "target_title": "Flow",
                "relation": "DEFINES",
                "evidence_quote": "Axiom",
            }],
        }
