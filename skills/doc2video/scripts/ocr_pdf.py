#!/usr/bin/env python3
"""给没有文字层的 PDF 补一层文字，让后面的链路照常跑。

扫描件、把幻灯片导成图片再拼起来的 PDF、截图拼的 PDF——这些页面上有字，但
**取不出字**。引擎不看图，`prepare.py` 会当场拒绝（「一个字都没解析出来」），
拒绝是对的：硬跑只会产出一份没有内容的讲稿。

这个脚本把每一页渲成位图、识一遍字，再把识出来的文字**按原位置**写回 PDF，
用不可见的渲染模式（`render_mode=3`）——**画面一个像素都不改**，改的只是
文字层。产出一份新 PDF，之后 `prepare.py --file <新 PDF>` 照常跑。

位置写回原位不是讲究：讲稿的每一句会绑到页面元素上，镜头照着元素的 bbox 决定
框哪里。文字全堆在页面左上角的话，视频里镜头就一直框左上角。

    python3 scripts/ocr_pdf.py --in 扫描件.pdf --out 扫描件-ocr.pdf
    python3 scripts/ocr_pdf.py --in a.pdf --out b.pdf --augment      # 图里的字也捞出来
    python3 scripts/ocr_pdf.py --in a.pdf --out b.pdf --pages 7,9    # 只补这两页

默认**只补那些取不出字的页**：一份 30 页里 3 页是截图，只有那 3 页会被识别，
其余 27 页的原文字层原样保留——原文字层永远比 OCR 准。

**`--augment` 是给图多的 deck 用的，值得默认打开一次看看。** 有文字层不等于
文字都在文字层里：架构图、产品截图、示意图里的字是画上去的，取不出来。
实测这份 30 页方案，第 25 页文字层 236 字、图里还有 855 字——**七成八的内容
在图里**，不捞出来那一页的讲稿就只能照着四分之一的内容写。
`--augment` 只补文字层里没有的行，已经有的跳过，不会讲两遍。

要装 OCR 引擎（约 150MB，模型随 wheel 打包，**识别时不联网**）：

    pip install rapidocr-onnxruntime
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

#: 一页少于这么多字，就当它没有文字层。纯图页通常是 0，但页眉页脚、水印、
#: 页码常常是真文字，于是「有 8 个字」的图片页并不少见。
MIN_CHARS = 20
#: 低于这个分的识别结果丢掉。OCR 在图标、装饰线、渐变底纹上会吐出短碎串。
MIN_SCORE = 0.5
#: 单字的行一律丢掉。图标旁边的一撇、边框的一角、渐变底纹，识出来就是「战」
#: 「图」这样的孤字——它们进了页面.md 就是噪声，而写讲稿的人要逐条判断它们。
MIN_LINE_CHARS = 2
#: 行首行尾的装饰符号，比对时剥掉。
_MARKS = "·•>《》<»«—–\\-_|丨:：,，.。、\"'"
_DECORATION = re.compile(f"^[{_MARKS}]+|[{_MARKS}]+$")
#: 渲染倍率。200 dpi 下 960×540 的幻灯片约 2667×1500，中文小字识得稳；
#: 再高一倍时间翻倍，准确率没有可测的提升。
DPI = 200


def main() -> int:
    parser = argparse.ArgumentParser(description="给没有文字层的 PDF 补一层可提取的文字")
    parser.add_argument("--in", dest="src", required=True, type=Path, help="原 PDF")
    parser.add_argument("--out", dest="dst", required=True, type=Path, help="产出的新 PDF")
    parser.add_argument("--pages", default="", help="只处理这几页，逗号分隔（默认：自动挑）")
    parser.add_argument(
        "--augment",
        action="store_true",
        help="有文字层的页也识一遍，只补文字层里没有的行（图里的字）",
    )
    parser.add_argument("--dpi", type=int, default=DPI, help=f"渲染倍率，默认 {DPI}")
    parser.add_argument(
        "--min-chars", type=int, default=MIN_CHARS, help=f"少于多少字算没有文字层，默认 {MIN_CHARS}"
    )
    args = parser.parse_args()

    if not args.src.exists():
        raise SystemExit(f"找不到 {args.src}")

    pymupdf = _pymupdf()
    engine = _engine()

    doc = pymupdf.open(args.src)
    only = {int(p) for p in args.pages.replace("，", ",").split(",") if p.strip()}

    todo: list[int] = []
    for index, page in enumerate(doc, start=1):
        if only and index not in only:
            continue
        if args.augment or len(page.get_text().strip()) < args.min_chars:
            todo.append(index)

    if not todo:
        print(
            f"{args.src.name} 的 {doc.page_count} 页都有文字层，不需要 OCR。\n"
            "直接跑 prepare.py 就行——原文字层比 OCR 准，不要多此一举。"
        )
        return 0

    print(f"{doc.page_count} 页，其中 {len(todo)} 页要识：{todo}")
    work = Path(tempfile.mkdtemp(prefix="d2v-ocr-"))
    total_lines = total_chars = 0
    for index in todo:
        page = doc[index - 1]
        had = len(page.get_text().strip())
        lines, chars, skipped = _ocr_page(pymupdf, engine, page, dpi=args.dpi, work=work)
        total_lines += lines
        total_chars += chars
        note = f"（原有 {had} 字，跳过已有的 {skipped} 行）" if had >= args.min_chars else ""
        print(f"  第 {index:>2} 页  补 {lines:>3} 行  {chars:>4} 字 {note}")
        if lines == 0 and not had:
            print("      一行都没识出来——这一页可能真的没有字（整页照片、图表底图）")

    args.dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(args.dst, garbage=3, deflate=True)
    print(
        f"\n写出 {args.dst}（{total_lines} 行 / {total_chars} 字）\n"
        f"接着跑：python3 scripts/prepare.py --file {args.dst} --brief \"…\" --out <工作目录>\n"
        "⚠️ OCR 出来的字**会被念出去**。写讲稿之前先看一眼 页面.md：\n"
        "   数字、机构名、专有名词识错了，讲稿就会照着错的讲，而画面上是对的。"
    )
    return 0


def _ocr_page(pymupdf, engine, page, *, dpi: int, work: Path) -> tuple[int, int, int]:
    """识一页，把文字层里没有的行按原位置写成不可见文字。

    返回 (补进去的行数, 字数, 因为已经有了而跳过的行数)。
    """
    # 这一页文字层里已经有什么。补进去之前逐行对一遍，否则同一句话会在
    # 页面.md 里出现两次，讲稿也就讲两遍。
    already = _flatten(page.get_text())
    pixmap = page.get_pixmap(dpi=dpi)
    image = work / f"p{page.number}.png"
    pixmap.save(image)
    result, _ = engine(str(image))
    image.unlink(missing_ok=True)
    if not result:
        return (0, 0, 0)

    scale = 72.0 / dpi
    lines = chars = skipped = 0
    for box, text, score in result:
        text = (text or "").strip()
        if len(text) < MIN_LINE_CHARS or float(score) < MIN_SCORE:
            continue
        flat = _flatten(text)
        if flat and flat in already:
            skipped += 1
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        rect = pymupdf.Rect(
            min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale
        )
        size = _fit(pymupdf, text, rect)
        if size <= 0:
            continue
        try:
            page.insert_text(
                # 基线在框底往上一点：写在框顶的话，提取出来的 bbox 会整体
                # 上移一行高，镜头就框到上面那一行去了。
                (rect.x0, rect.y1 - rect.height * 0.18),
                text,
                fontname="china-s",  # PyMuPDF 自带的中文字体，不必外挂字体文件
                fontsize=size,
                render_mode=3,  # 不可见：画面一个像素都不改，只是能取出字
            )
        except Exception as exc:  # noqa: BLE001 - 单行失败不该毁掉整页
            print(f"      这一行写不进去，跳过：{str(exc)[:80]}")
            continue
        lines += 1
        chars += len(text)
        already += flat
    return (lines, chars, skipped)


def _flatten(text: str) -> str:
    """比对用的写法：去掉空白，以及行首行尾的装饰符号。

    OCR 把页面上的项目符号一起识了出来——「·行业技术标准与规范」对上文字层里的
    「行业技术标准与规范」，不剥掉这个点，同一条就会被当成新内容补进去。
    """
    stripped = re.sub(r"\s+", "", text)
    return _DECORATION.sub("", stripped)


def _fit(pymupdf, text: str, rect) -> float:
    """让这行字的宽度贴着识别框——bbox 对不对，决定镜头框得准不准。"""
    size = max(rect.height * 0.8, 1.0)
    width = pymupdf.get_text_length(text, fontname="china-s", fontsize=size)
    if width <= 0:
        return 0.0
    return max(1.0, min(size * rect.width / width, rect.height * 1.2))


def _pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        raise SystemExit("没装 pymupdf：pip install pymupdf") from None


def _engine():
    """OCR 引擎。选 rapidocr-onnxruntime 的理由写在 requirements.txt 里：
    模型随 wheel 打包，**识别时不联网**——材料不出域是硬要求，不是偏好。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print(
            "没装 OCR 引擎。装它（约 150MB，之后完全离线）：\n"
            "    pip install rapidocr-onnxruntime\n"
            "不想装的话，只能换一份带文字层的原文件——引擎不看图。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    return RapidOCR()


if __name__ == "__main__":
    raise SystemExit(main())
