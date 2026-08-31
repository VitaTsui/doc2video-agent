#!/usr/bin/env python3
"""开工前的体检：这台机器能出什么样的片子。

逐项报当前环境有什么、缺什么、缺了会怎样。四档能力互相独立，缺一档只是那一档
降级，不会让整条链路失败——**但降级是静默的**：没有配音引擎照样出片，只是哑的；
没有渲染器照样有工程，只是没有 mp4。所以这一步不做，后面出了怪结果会被归到
「材料有问题」上去，而它其实是环境的问题。

最要紧的一项是**播音腔**。它在别人的机器上（微软的 read-aloud 接口），装得上
不等于连得通，所以默认会真合成一句话来试，而不是只 import 一下。

    python3 scripts/check_env.py              # 含联网试音
    python3 scripts/check_env.py --no-probe   # 不试音，只看装没装
    python3 scripts/check_env.py --strict     # 缺关键能力时退出码非零
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.workspace import VOICE, bootstrap  # noqa: E402

OK, BAD, WARN = "✓", "✗", "!"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查这台机器能出什么样的片子")
    parser.add_argument("--no-probe", action="store_true", help="不联网试音，只看装没装")
    parser.add_argument("--strict", action="store_true", help="缺关键能力时退出码非零")
    args = parser.parse_args()

    # 体检也要在同一套环境变量下做，否则查的是另一台机器的配置。
    bootstrap(Path(tempfile.mkdtemp(prefix="d2v-check-")))

    print(f"Python {sys.version.split()[0]}｜{sys.platform}")

    fatal: list[str] = []
    if not _engine(fatal):
        return 1
    print()
    _voice(fatal, probe=not args.no_probe)
    print()
    _node(fatal)
    print()
    _binaries(fatal)
    print()
    _subtitle_font(fatal)
    print()
    _slides()
    print()
    _ocr()

    if fatal:
        print("\n必须先解决的：")
        for line in fatal:
            print(f"  {BAD} {line}")
        return 1 if args.strict else 0
    print("\n可以开工。")
    return 0


def _engine(fatal: list[str]) -> bool:
    try:
        import doc2video

        where = Path(doc2video.__file__).parent
        print(f"{OK} doc2video 引擎（{where}）")
        return True
    except ImportError as exc:
        here = Path(__file__).resolve().parent.parent
        print(f"{BAD} doc2video 引擎没装：{exc}")
        print(f"    pip install {here / 'vendor'}/doc2video_agent-*.whl")
        print(f"    pip install -r {here / 'requirements.txt'}")
        fatal.append("引擎没装，其余检查都做不了")
        return False


def _voice(fatal: list[str], *, probe: bool) -> None:
    """配音。这一项没有降级的余地——静音成片是废品，不是次品。"""
    from doc2video.tools.tts.providers import SilentProvider, resolve_provider

    print("配音：")
    provider = resolve_provider("edge")
    if provider.name != "edge":
        reason = getattr(type(provider)(), "unavailable_reason", lambda: "")()
        print(f"{BAD} 播音腔（edge-tts）不可用，落到了 {provider.name}")
        print(f"    {reason or '未安装 edge-tts'}")
        print("    pip install edge-tts")
        fatal.append("装 edge-tts，否则出来的不是播音腔")
        if isinstance(provider, SilentProvider):
            fatal.append("当前会合成静音——成片是哑的，时间轴和字幕仍然正确")
        return

    print(f"{OK} 播音腔 {VOICE}｜{provider.chars_per_second} 字/秒（分镜阶段的粗估按这个算，实际时长以合成结果为准）")
    if not probe:
        print(f"{WARN} 没试音：装上了不等于连得通，出片前至少试一次")
        return

    # 真合成一句。这个接口在墙外，装得上和连得通是两件事，而连不通的表现是
    # 渲染到配音那一步才失败——那时候已经解析完整份文档了。
    out = Path(tempfile.mkdtemp(prefix="d2v-voice-")) / "probe.wav"
    try:
        seconds = provider.synthesize(
            "这是一次试音。", out, voice=VOICE, rate=provider.natural_rate
        )
        print(f"{OK} 试音成功｜{seconds:.2f} 秒｜{out.stat().st_size // 1024} KB")
    except Exception as exc:  # noqa: BLE001 - 网络、接口、编码都可能
        print(f"{BAD} 试音失败：{str(exc)[:160]}")
        print("    多半是沙箱连不到 speech.platform.bing.com。没有外网就出不了播音腔，")
        print("    这一点没有本地替代——先解决网络，不要退而求其次。")
        fatal.append("播音腔连不通")
    finally:
        shutil.rmtree(out.parent, ignore_errors=True)


def _binaries(fatal: list[str]) -> None:
    """ffmpeg / ffprobe。没有 ffmpeg 就没有 mp4，前面所有阶段照跑照落盘。"""
    from doc2video.tools import media_binaries

    print("外部二进制：")
    ffmpeg = media_binaries.ffmpeg()
    if ffmpeg.available:
        print(f"{OK} ffmpeg   [{ffmpeg.source}] {ffmpeg.path}")
    else:
        print(f"{BAD} ffmpeg   没有——出不了成片")
        print("    pip install ffmpeg-binaries      # Linux x86_64 / macOS / Windows")
        print("    pip install imageio-ffmpeg       # Linux arm64")
        fatal.append("装 ffmpeg，否则渲染阶段必失败")

    ffprobe = media_binaries.ffprobe()
    print(
        f"{OK} ffprobe  [{ffprobe.source}] {ffprobe.path}"
        if ffprobe.available
        else f"{WARN} ffprobe  没有——改用 WAV 头/解析 ffmpeg 输出测时长，结果一样"
    )
    if ffmpeg.available:
        print(
            f"{OK} drawtext 滤镜｜字幕会烧进画面"
            if media_binaries.has_filter("drawtext")
            else f"{WARN} 没有 drawtext 滤镜｜这次没有字幕，其余照常渲染"
        )


def _subtitle_font(fatal: list[str]) -> None:
    """字幕字体。没有中文字形的字体会把每个汉字画成方块——比没有字幕更糟。"""
    from doc2video.tools.parsers.slide_raster import chinese_font, font_candidates

    print("字幕字体：")
    font = chinese_font()
    if font:
        print(f"{OK} {font}")
        return
    existing = [c for c in font_candidates() if Path(c).exists()]
    print(f"{WARN} 没有能画汉字的字体（找到 {len(existing)} 个字体，都没有中文字形）")
    print("    这次不会烧字幕——**不是**烧成方块，引擎会跳过并记一条降级。")
    print("    要字幕就给它一个中文字体，三选一：")
    print("      apt-get install -y fonts-noto-cjk          # 有 root 的话最省事")
    print("      把 .otf/.ttf 放进技能包的 vendor/fonts/     # 脚本会自动用它")
    print("      export D2V_FONT_PATH=/path/to/NotoSansSC-Regular.otf")


def _slides() -> None:
    """幻灯片渲染档位。这一档决定了 PPT 出来长什么样，PDF 不受影响。"""
    from doc2video.core.config import which

    print("幻灯片渲染（只影响 PPT/PPTX，PDF 由 PyMuPDF 自己渲染）：")
    if which("soffice") or which("libreoffice"):
        print(f"{OK} LibreOffice｜最高保真：PowerPoint 自己的排版引擎")
        return
    node = which("node")
    print(f"{WARN} 没有 LibreOffice")
    if node:
        print(f"{WARN} 有 Node，但要 renderer/ 装过依赖才有 Chromium 档")
    print("    沙箱里通常两档都没有，PPT 会退到内置栅格化器（只按 shape 几何画，保真度低）。")
    print("    **所以沙箱里优先要 PDF**：让用户把 PPT 导出成 PDF 再送进来，")
    print("    页面是原样的，比在这里补渲染依赖省事得多。")


def _ocr() -> None:
    """OCR。只有扫描件 / 图片 PDF 要它，所以缺了不算 fatal——但要说清楚。"""
    print("OCR（只有没有文字层的 PDF 要它）：")
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        print(f"{WARN} 没装 rapidocr-onnxruntime")
        print("    带文字层的 PDF 和 PPTX 不需要它。遇到扫描件 / 整页是图的 PDF 再装：")
        print("    pip install rapidocr-onnxruntime      # 约 150MB，之后识别不联网")
        return
    print(f"{OK} rapidocr-onnxruntime｜python3 scripts/ocr_pdf.py --in a.pdf --out b.pdf")


def _node(fatal: list[str]) -> None:
    """画面是 Remotion 渲的，Node 是硬依赖——这一版和上一版最大的环境差别。

    上一版只要 Python 和 ffmpeg 就能出片。现在缺了 Node，前面每一步都会成功，
    一直到 `init_project.py` 才失败——那时文档已经解析完、分镜可能都写好了。
    """
    print("画面（Remotion）：")
    node = shutil.which("node")
    if node is None:
        print(f"{BAD} 没有 node。这一版的画面是 Remotion 渲的，不装就出不了片")
        print("    装 Node 18+（沙箱里通常预装，没有就找平台方）")
        fatal.append("Node 没装，无法渲染")
        return

    try:
        raw = subprocess.run(  # noqa: S603
            [node, "--version"], capture_output=True, text=True, timeout=20
        ).stdout.strip()
        major = int(raw.lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"{WARN} node 在（{node}），但问不出版本：{str(exc)[:80]}")
        return

    if major < 18:
        print(f"{BAD} node {raw}——Remotion 4 要 18 以上")
        fatal.append(f"node {raw} 太老，Remotion 4 要 18+")
        return
    print(f"{OK} node {raw}")

    if shutil.which("npm") is None:
        print(f"{BAD} 有 node 但没有 npm，装不了 Remotion 依赖")
        fatal.append("npm 没装")
        return
    print(f"{OK} npm 在（init_project.py 用它装依赖，实测约一分钟）")


if __name__ == "__main__":
    raise SystemExit(main())
