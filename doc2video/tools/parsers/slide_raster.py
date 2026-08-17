"""Fallback slide rasterizer.

LibreOffice gives pixel-accurate slide renders; when it is not installed we still
need *something* to show on screen. This draws each slide from its parsed shape
geometry with Pillow: correct layout, plain styling. It is deliberately simple —
the point is that the pipeline stays runnable end to end without LibreOffice.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...schemas import DocumentPage, ElementKind

BACKGROUND = (252, 252, 250)
TITLE_COLOR = (24, 28, 38)
BODY_COLOR = (55, 62, 76)
ACCENT = (196, 106, 70)

# macOS / common Linux CJK-capable fonts, best first.
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def rasterize_page(page: DocumentPage, out_path: Path) -> None:
    """Draw one page's elements onto a PNG at the page's declared pixel size."""
    width, height = int(page.width or 1920), int(page.height or 1080)
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    # A thin accent bar keeps slides from looking like blank pages.
    draw.rectangle([0, 0, width, max(6, height // 180)], fill=ACCENT)

    for element in page.elements:
        box = element.bbox
        if element.kind is ElementKind.IMAGE and element.asset_path:
            _paste_image(canvas, element.asset_path, box)
            continue
        if not element.text:
            continue
        is_title = element.kind in (ElementKind.TITLE, ElementKind.SUBTITLE)
        font_size = _fit_font_size(box.h, is_title=is_title)
        font = _load_font(font_size)
        color = TITLE_COLOR if is_title else BODY_COLOR
        _draw_wrapped(draw, element.text, box, font, color)

    canvas.save(out_path)


def _fit_font_size(box_height: float, *, is_title: bool) -> int:
    base = 54 if is_title else 34
    # Cap by the box so long bodies do not overflow their placeholder.
    return max(18, min(base, int(box_height * 0.55) or base))


def _draw_wrapped(draw, text: str, box, font, color) -> None:
    max_width = max(40, int(box.w))
    lines: list[str] = []
    for raw_line in text.split("\n"):
        current = ""
        for char in raw_line:
            trial = current + char
            if draw.textlength(trial, font=font) > max_width and current:
                lines.append(current)
                current = char
            else:
                current = trial
        lines.append(current)

    line_height = int(font.size * 1.35)
    y = int(box.y)
    limit = int(box.y + box.h) + line_height
    for line in lines:
        if y > limit:
            break
        draw.text((int(box.x), y), line, font=font, fill=color)
        y += line_height


def _paste_image(canvas: Image.Image, asset_path: str, box) -> None:
    path = Path(asset_path)
    if not path.exists():
        return
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            target = (max(1, int(box.w)), max(1, int(box.h)))
            canvas.paste(img.resize(target), (int(box.x), int(box.y)))
    except OSError:
        return
