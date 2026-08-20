"""Measure another PDF parser against the one this project uses.

Written for a specific question — *should Docling replace `pdf_parser`?* — and
kept because the question will be asked again about the next library. It is not
a document-parsing benchmark: the numbers that decide it here are not the ones
a general benchmark reports.

What matters to this project, in order:

1. **Do the boxes land on the rendered page?** An element exists so a camera
   can be aimed at it. A parser that reads structure beautifully and reports it
   in some other coordinate system has done half a job, and the other half is
   ours to write.
2. **Recall on the things that get pointed at** — the text blocks and figures a
   narrator names. Not raw element count: a parser that splits a paragraph into
   nine spans scores well and helps nobody.
3. **Cost.** This runs inside a desktop app whose runtime is already 400MB and
   whose first launch is already the slowest thing about it. Seconds and
   megabytes are features here, not footnotes.

Usage::

    uv run --with docling --no-project python scripts/bench_parser.py deck.pdf

Docling is not a dependency of this project and must not become one to run
this; `--with` puts it in a throwaway environment.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path


def peak_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024


def run_ours(pdf: Path) -> dict:
    from doc2video.tools.parsers.pdf_parser import parse_pdf

    assets = Path(tempfile.mkdtemp())
    started = time.time()
    document = parse_pdf(pdf, assets, target_width=1920)
    return {
        "name": "pdf_parser (pymupdf)",
        "seconds": round(time.time() - started, 1),
        "peak_mb": round(peak_mb()),
        "pages": len(document.pages),
        "elements": sum(len(page.elements) for page in document.pages),
        "per_page": [len(page.elements) for page in document.pages],
        # Already in rendered-page pixels, top-left origin: the same space the
        # director, the highlight boxes and the review checks all work in.
        "coordinates": "rendered pixels, top-left",
        "kinds": _count(element.kind.value for page in document.pages for element in page.elements),
    }


def run_docling(pdf: Path) -> dict:
    from docling.document_converter import DocumentConverter

    started = time.time()
    result = DocumentConverter().convert(str(pdf))
    seconds = round(time.time() - started, 1)
    document = result.document

    per_page: dict[int, int] = {}
    kinds: list[str] = []
    for item, _level in document.iterate_items():
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        per_page[prov[0].page_no] = per_page.get(prov[0].page_no, 0) + 1
        kinds.append(str(item.label))

    return {
        "name": "docling",
        "seconds": seconds,
        "peak_mb": round(peak_mb()),
        "pages": len(document.pages),
        "elements": sum(per_page.values()),
        "per_page": [per_page.get(i, 0) for i in sorted(per_page)],
        # Points, bottom-left origin, against the PDF's own page box — every
        # box needs remapping before anything here can aim at it.
        "coordinates": "pdf points, bottom-left",
        "kinds": _count(kinds),
    }


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: -pair[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    results = [run_ours(args.pdf)]
    try:
        results.append(run_docling(args.pdf))
    except ImportError:
        print("docling 没装，只跑本项目的解析器（用 --with docling 装到临时环境）")

    for result in results:
        print(
            f"{result['name']:<22} {result['pages']:>3} 页 "
            f"{result['elements']:>4} 元素 {result['seconds']:>7.1f}s "
            f"峰值 {result['peak_mb']:>5}MB  坐标：{result['coordinates']}"
        )
        print(f"{'':<22} 类型 {result['kinds']}")

    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
