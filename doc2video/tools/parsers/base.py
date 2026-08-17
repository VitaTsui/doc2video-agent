"""Parser dispatch. One entry point, one output shape.

Parsers produce a *structural* DocumentModel: pages, elements, bounding boxes,
speaker notes and a rendered image per page. They never call an LLM — semantic
enrichment is the document skill's job.
"""

from __future__ import annotations

from pathlib import Path

from ...core.errors import UnsupportedSource
from ...schemas import DocumentModel, SourceType

PDF_SUFFIXES = {".pdf"}
PPT_SUFFIXES = {".ppt", ".pptx"}


def detect_source_type(path: Path) -> SourceType:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return SourceType.PDF
    if suffix == ".pptx":
        return SourceType.PPTX
    if suffix == ".ppt":
        return SourceType.PPT
    raise UnsupportedSource(
        f"不支持的文件类型：{suffix or path.name}", detail={"supported": [".pdf", ".ppt", ".pptx"]}
    )


def parse(path: Path, assets_dir: Path, *, target_width: int = 1920) -> DocumentModel:
    """Parse a source document into a structural DocumentModel.

    ``assets_dir`` receives one rendered PNG per page plus any extracted images.
    """
    source_type = detect_source_type(path)
    assets_dir.mkdir(parents=True, exist_ok=True)

    if source_type is SourceType.PDF:
        from .pdf_parser import parse_pdf

        return parse_pdf(path, assets_dir, target_width=target_width)

    from .ppt_parser import parse_ppt

    return parse_ppt(path, assets_dir, target_width=target_width)
