"""Document parsers: PDF via PyMuPDF, PPT/PPTX via python-pptx."""

from .base import detect_source_type, parse

__all__ = ["detect_source_type", "parse"]
