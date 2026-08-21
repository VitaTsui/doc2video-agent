"""The application icon, drawn rather than stored.

An icon is a handful of rectangles and a triangle; keeping it as code means
the seven sizes can never drift apart, and changing the accent colour is one
line rather than a round trip through a drawing program.

The shape: a page, and inside it the thing this product does to a page — two
lines of text and a play button. Both halves are needed. A play triangle alone
is every video app ever made, and a page alone is a document viewer.

Everything is laid out on a 1024 grid and drawn at four times the target size
before being reduced: Pillow fills shapes without antialiasing, and an icon
with stepped edges reads as a cheap icon at exactly the sizes people see most.

Run `python desktop/web/scripts/gen-app-icon.py` after changing anything here.
"""

from pathlib import Path

from PIL import Image, ImageDraw

TERRA = (201, 100, 66, 255)  # --accent, the same terracotta as the window
CREAM = (250, 249, 245, 255)  # --bg

ICONS = Path(__file__).resolve().parents[2] / "src-tauri" / "icons"
# What tauri.conf.json lists, plus the two the bundler picks up by name.
PNGS = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "256x256.png": 256,
    "512x512.png": 512,
    "icon.png": 1024,
}
# Windows wants several sizes in the one file; 16 is the taskbar's.
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def draw(size: int) -> Image.Image:
    scale = 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    unit = canvas / 1024

    def box(x: float, y: float, w: float, h: float, radius: float, fill=TERRA) -> None:
        pen.rounded_rectangle(
            [x * unit, y * unit, (x + w) * unit, (y + h) * unit], radius=radius * unit, fill=fill
        )

    # The plate. 22.5% corner radius is what macOS uses, and Windows crops
    # nothing, so the same shape works on both.
    box(0, 0, 1024, 1024, 230)
    # The page: portrait, because portrait is what reads as a document.
    box(272, 168, 480, 688, 56, CREAM)
    # Two lines of text, the second short, so it reads as prose and not as a
    # table. They are the only detail that disappears at 16px, and the icon
    # still works when it does.
    box(336, 264, 352, 52, 26)
    box(336, 372, 240, 52, 26)
    # The play button. Shifted right of centre by the difference between a
    # triangle's bounding box and where the eye puts its middle — centred by
    # its box, it looks left-heavy.
    pen.polygon(
        [(438 * unit, 500 * unit), (438 * unit, 740 * unit), (662 * unit, 620 * unit)],
        fill=TERRA,
    )
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    for name, size in PNGS.items():
        draw(size).save(ICONS / name)
        print(f"{name}  {size}x{size}")
    largest = draw(256)
    largest.save(ICONS / "icon.ico", sizes=[(s, s) for s in ICO_SIZES])
    print(f"icon.ico  {', '.join(str(s) for s in ICO_SIZES)}")


if __name__ == "__main__":
    main()
