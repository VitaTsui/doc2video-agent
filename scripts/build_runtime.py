"""Build the runtime the desktop app downloads on first launch.

The shell is a few megabytes; everything that actually does the work — a Python
interpreter with the pipeline installed, ffmpeg, a neural voice, a CJK font,
and optionally Node with the Remotion project — is this. It is kept out of the
installer deliberately: bundling it would make every app update re-download
~600MB to change a button, and the two version at completely different rates.

**It ships as two pieces, because they change at completely different rates.**

    base   the interpreter, every third-party dependency, node, the browser,
           the voice, the font. ~400MB, and unchanged for months at a time.
    app    doc2video itself and the Remotion sources. ~2MB, and different on
           every single release.

Before the split every release forced every user through the whole 400MB
again — and through unpacking twenty thousand files, which on Windows is the
slower half. The base's version is a hash of what determines it (the declared
dependencies, the lockfile, the pinned Node and Python, the voice, the font),
never a number someone bumps by hand: a stale hash would ship a new app into a
tree without its new dependencies, and that fails at the first render rather
than at install time.

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


def base_version() -> str:
    """A digest of everything that decides what the heavy half contains.

    Hand-maintained would be wrong here, and wrong in the expensive direction:
    forget to bump it after adding a dependency and users keep a base that has
    no such package, while the app that needs it installs happily. The failure
    then surfaces as an ImportError in the middle of someone's first render.
    """
    material = [
        PYTHON_VERSION,
        NODE_VERSION,
        VOICE,
        FONT_URL,
        _dependency_block(),
        _read(ROOT / "renderer" / "pnpm-lock.yaml"),
        _read(ROOT / "renderer" / "package.json"),
    ]
    return hashlib.sha256("\n".join(material).encode("utf-8")).hexdigest()[:12]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _dependency_block() -> str:
    """The declared dependencies, without the parts that do not affect them.

    Taken as text between the first ``dependencies`` key and the end of the
    optional-dependency tables. Reading it with a TOML parser would be tidier,
    but this file is also read by the Rust side's test, and a digest that two
    languages must agree on is safest computed one way, in one place.
    """
    text = _read(ROOT / "pyproject.toml")
    start = text.find("dependencies = [")
    if start == -1:
        return text
    end = text.find("[tool.", start)
    return text[start : end if end != -1 else len(text)]


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


def site_packages(python: Path) -> Path:
    """Where pip put things. Windows lays this out differently from the rest."""
    if os.name == "nt":
        return python / "Lib" / "site-packages"
    return python / "lib" / f"python{PYTHON_VERSION}" / "site-packages"


def _write(archive: Path, digest_of: Path | None = None) -> Path:
    """A checksum beside the archive. The app verifies before it unpacks."""
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix("").with_suffix(".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    print(f"  {archive.name}  {archive.stat().st_size / 1024 / 1024:.1f}MB")
    print(f"  sha256 {digest}")
    return archive


def build_app_only(out: Path) -> None:
    """Just the files the app half carries — no interpreter, no dependencies.

    This is what makes an ordinary release cheap: when the base digest already
    exists, nothing has to install Python, resolve a lockfile or fetch a
    browser. `pip --no-deps --target` is doing very little work here, but it is
    what produces a real dist-info, and the API reads its own version from one.
    """
    print("== 只打 app ==")
    packages = site_packages(out / "python")
    packages.mkdir(parents=True, exist_ok=True)
    # `uv pip` rather than `pip`: the interpreter running this script is often
    # a uv-managed venv, which ships no pip at all.
    run("uv", "pip", "install", "--quiet", "--no-deps", "--target", str(packages), str(ROOT))
    renderer = ROOT / "renderer" / "src"
    if renderer.exists():
        shutil.copytree(renderer, out / "node" / "src", dirs_exist_ok=True)


def pack_base(out: Path) -> Path:
    """The heavy half, named by what it contains rather than by a release.

    Named by digest on purpose: two releases whose dependencies did not move
    resolve to the same file, and the second one does not have to build it,
    upload it, or make anybody download it again.
    """
    print("== 打包 base ==")
    archive = out.parent / f"d2v-base-{base_version()}-{target()}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname="runtime")
    return _write(archive)


def pack_app(out: Path, version: str) -> Path:
    """doc2video itself, and the Remotion sources. Everything else is base.

    Two megabytes against four hundred — this is the piece that actually
    changes when a release changes, and making it the only piece a user
    fetches is the whole point of the split.
    """
    print("== 打包 app ==")
    packages = site_packages(out / "python")
    prefix = packages.relative_to(out)

    archive = out.parent / f"d2v-app-{version}-{target()}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(packages / "doc2video", arcname=f"runtime/{prefix}/doc2video")
        # The dist-info too: without it `importlib.metadata.version` raises,
        # and the API reports its own version from there.
        for info in packages.glob("doc2video_agent-*.dist-info"):
            tar.add(info, arcname=f"runtime/{prefix}/{info.name}")
        renderer = out / "node" / "src"
        if renderer.exists():
            tar.add(renderer, arcname="runtime/node/src")
        # The manifest rides along, so unpacking the app is what records that
        # this tree now holds this version. Nothing else has to write it.
        tar.add(out / "runtime.json", arcname="runtime/runtime.json")
    return _write(archive)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建桌面版的运行时包")
    parser.add_argument("--out", default="dist/runtime", help="输出目录")
    parser.add_argument("--version", default="", help="版本号，默认读 pyproject")
    parser.add_argument("--no-renderer", action="store_true", help="不带 Node / Remotion")
    parser.add_argument("--no-pack", action="store_true", help="只构建，不打包")
    parser.add_argument(
        "--part",
        choices=("all", "base", "app"),
        default="all",
        help="打哪一半。app 不需要装依赖，几秒钟就好",
    )
    parser.add_argument(
        "--print-base-version", action="store_true", help="只打印 base 的版本号后退出"
    )
    args = parser.parse_args()

    if args.print_base_version:
        print(base_version())
        return 0

    version = args.version or _project_version()
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    if args.part == "app":
        build_app_only(out)
    else:
        build_python(out)
        build_voice(out, out / "python")
        build_font(out)
        if not args.no_renderer:
            build_node(out)

    manifest = {
        # Kept for the runtimes already installed out there, which know only
        # this key and must read as "wrong version" rather than as corrupt.
        "version": version,
        "base": base_version(),
        "app": version,
        "target": target(),
        "renderer": not args.no_renderer,
    }
    (out / "runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n运行时 {size / 1024 / 1024:.0f}MB → {out}")
    if not args.no_pack:
        if args.part in ("all", "base"):
            pack_base(out)
        if args.part in ("all", "app"):
            pack_app(out, version)
    return 0


def _project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    return "0.0.0"


if __name__ == "__main__":
    sys.exit(main())
