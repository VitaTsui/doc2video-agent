"""Build the runtime the desktop app downloads on first launch.

The shell is a few megabytes; everything that actually does the work — a Python
interpreter with the pipeline installed, ffmpeg, a neural voice, a CJK font,
and optionally Node with the Remotion project — is this. It is kept out of the
installer deliberately: bundling it would make every app update re-download
~600MB to change a button, and the two version at completely different rates.

Layout, which `sidecar.rs` and the settings agree on:

    runtime/
      python/   a standalone interpreter with the pipeline installed into it
      node/     the Remotion workspace, with node_modules and a node binary
      voices/   *.onnx for Piper
      fonts/    a CJK font, so a clean Windows or a slim container is not tofu

Run it on the platform you are building for: the interpreter, ffmpeg wheel, Remotion's
native compositor and Node binary are all per-platform, and none of them can be
cross-built here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Windows consoles default to a legacy code page, and every message here is in
# Chinese: without this the script dies on its first print rather than on
# anything to do with building a runtime.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

NODE_VERSION = "22.14.0"
PYTHON_VERSION = "3.12"
FONT_URL = (
    "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/"
    "NotoSansCJKsc-Regular.otf"
)
VOICE = "zh_CN-huayan-medium"


def target() -> str:
    """The name this runtime is published under, matching Rust's target triple
    closely enough that the app can ask for its own."""
    machine = {"x86_64": "x64", "AMD64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine(), platform.machine()
    )
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[platform.system()]
    return f"{system}-{machine}"


def run(*args: str, cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"  $ {' '.join(str(a) for a in args)}")
    command = [str(a) for a in args]
    # On Windows `pnpm`, `npx` and `uv` are .cmd shims, which CreateProcess
    # will not execute by that bare name — it reports "cannot find the file"
    # for a command that is plainly on PATH.
    resolved = shutil.which(command[0])
    if resolved:
        command[0] = resolved
    subprocess.run(command, cwd=cwd, check=True, env=env)


def build_python(out: Path) -> None:
    """A complete interpreter, not a virtual environment.

    A venv — even ``uv venv --relocatable`` — records the interpreter it was
    built against in ``pyvenv.cfg``. On this machine that came out as
    ``/opt/homebrew/opt/python@3.12/bin``, so the runtime would have needed the
    build machine's Homebrew Python to exist on the *user's* machine. What
    ships instead is a python-build-standalone tree with the pipeline installed
    into it: nothing outside the directory, verified by running it under
    ``env -i``.
    """
    print("== Python 运行时 ==")
    staging = out.parent / ".python-download"
    shutil.rmtree(staging, ignore_errors=True)
    run(
        "uv",
        "python",
        "install",
        PYTHON_VERSION,
        env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(staging)},
    )

    # uv leaves both a versioned directory and a shorthand symlink; the
    # versioned one is the real tree.
    source = max(
        (p for p in staging.iterdir() if p.is_dir() and not p.is_symlink()),
        key=lambda p: len(p.name),
    )
    python = out / "python"
    shutil.copytree(source, python, symlinks=True)
    shutil.rmtree(staging, ignore_errors=True)

    # python-build-standalone marks itself externally managed, which is right
    # for a system interpreter and wrong for one we own outright.
    for marker in python.rglob("EXTERNALLY-MANAGED"):
        marker.unlink()

    run(_python_bin(python), "-m", "pip", "install", "--quiet", f"{ROOT}[bundled,llm]")


def _python_bin(root: Path) -> Path:
    return root / ("python.exe" if os.name == "nt" else "bin/python3")


def build_voice(out: Path, venv: Path) -> None:
    """Ship the voice, rather than making first render wait on 61MB."""
    print("== 语音模型 ==")
    voices = out / "voices"
    voices.mkdir(parents=True, exist_ok=True)
    try:
        run(_python_bin(venv), "-m", "piper.download_voices", VOICE, "--download-dir", voices)
    except subprocess.CalledProcessError:
        # macOS has no piper (its wheel's espeak data path is broken), so there
        # is nothing to download there and nothing is lost: `say` covers it.
        print("  跳过：这个平台没有安装 piper-tts")


def build_font(out: Path) -> None:
    """A CJK font of our own, because the machine may have none.

    Without it a clean Windows loses its burned-in subtitles and a slim Linux
    container renders every character as a box.
    """
    print("== 字体 ==")
    fonts = out / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    target_path = fonts / "NotoSansCJKsc-Regular.otf"
    if not target_path.exists():
        print(f"  下载 {FONT_URL.rsplit('/', 1)[-1]}")
        urllib.request.urlretrieve(FONT_URL, target_path)  # noqa: S310


def build_node(out: Path) -> None:
    """Node plus the Remotion workspace, with its browser already fetched.

    Skippable: without it the app renders through ffmpeg, which produces the
    same video with plainer slides. With it the runtime roughly doubles.
    """
    print("== Node / Remotion ==")
    node_dir = out / "node"
    node_dir.mkdir(parents=True, exist_ok=True)

    # pnpm-workspace.yaml comes too: it is where esbuild is allowed to run its
    # install script, and pnpm treats a blocked script as a hard error — the
    # copied directory would fail to install without it.
    for name in (
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "tsconfig.json",
        "remotion.config.ts",
    ):
        source = ROOT / "renderer" / name
        if source.exists():
            shutil.copy2(source, node_dir / name)
    shutil.copytree(ROOT / "renderer" / "src", node_dir / "src", dirs_exist_ok=True)

    run("pnpm", "install", "--prod=false", cwd=node_dir)
    # Fetch the headless browser now; doing it at first render adds ~165MB to
    # what already looks like a hang.
    run("npx", "remotion", "browser", "ensure", cwd=node_dir)

    print("  下载 Node 运行时")
    system = {"macos": "darwin", "linux": "linux", "windows": "win"}[target().split("-")[0]]
    arch = target().split("-")[1]
    if system == "win":
        print("  跳过：Windows 的 node 需要 zip，请在 CI 里单独处理")
        return
    url = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-{system}-{arch}.tar.gz"
    archive = out / "node.tar.gz"
    urllib.request.urlretrieve(url, archive)  # noqa: S310
    with tarfile.open(archive) as tar:
        tar.extractall(out, filter="data")
    extracted = out / f"node-v{NODE_VERSION}-{system}-{arch}"
    shutil.copytree(extracted / "bin", node_dir / "bin", dirs_exist_ok=True)
    shutil.rmtree(extracted)
    archive.unlink()


def pack(out: Path, version: str) -> Path:
    """One archive, one checksum. The app verifies before it unpacks."""
    print("== 打包 ==")
    name = f"d2v-runtime-{version}-{target()}"
    archive = out.parent / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname="runtime")

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out.parent / f"{name}.sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"  {archive.name}  {archive.stat().st_size / 1024 / 1024:.0f}MB")
    print(f"  sha256 {digest}")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="构建桌面版的运行时包")
    parser.add_argument("--out", default="dist/runtime", help="输出目录")
    parser.add_argument("--version", default="", help="版本号，默认读 pyproject")
    parser.add_argument("--no-renderer", action="store_true", help="不带 Node / Remotion")
    parser.add_argument("--no-pack", action="store_true", help="只构建，不打包")
    args = parser.parse_args()

    version = args.version or _project_version()
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    build_python(out)
    build_voice(out, out / "python")
    build_font(out)
    if not args.no_renderer:
        build_node(out)

    manifest = {
        "version": version,
        "target": target(),
        "renderer": not args.no_renderer,
    }
    (out / "runtime.json").write_text(json.dumps(manifest, indent=2))

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n运行时 {size / 1024 / 1024:.0f}MB → {out}")
    if not args.no_pack:
        pack(out, version)
    return 0


def _project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    return "0.0.0"


if __name__ == "__main__":
    sys.exit(main())
