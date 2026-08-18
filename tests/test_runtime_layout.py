"""The layout the runtime and the shell have to agree on.

The desktop app downloads a runtime and runs the pipeline out of it. Three
directories inside it are addressed by name from three different languages —
the build script writes them, Rust points the backend at them, Python reads
them — so a rename in one place is a silent failure in another: no voice, tofu
subtitles, or a backend that will not start at all.
"""

from __future__ import annotations

from pathlib import Path

from doc2video.core.config import Settings


def test_the_fonts_directory_sits_beside_the_node_workspace(tmp_path: Path, monkeypatch):
    """`runtime/fonts` is found relative to `node_dir`, which the shell sets."""
    from doc2video.core import config
    from doc2video.tools.parsers import slide_raster

    runtime = tmp_path / "runtime"
    (runtime / "fonts").mkdir(parents=True)
    (runtime / "fonts" / "NotoSansCJKsc-Regular.otf").write_bytes(b"x")
    monkeypatch.setattr(config, "get_settings", lambda: Settings(node_dir=runtime / "node"))

    assert slide_raster.bundled_fonts_dir() == runtime / "fonts"
    assert slide_raster.font_candidates()[0].endswith("NotoSansCJKsc-Regular.otf")


def test_the_build_script_and_the_shell_name_the_same_directories():
    """Rust looks for `runtime/python` and `runtime/node`; the script writes them."""
    script = (Path(__file__).parent.parent / "scripts" / "build_runtime.py").read_text("utf-8")
    sidecar = (
        Path(__file__).parent.parent / "desktop" / "src-tauri" / "src" / "sidecar.rs"
    ).read_text("utf-8")

    for name in ("python", "node"):
        assert f'out / "{name}"' in script, f"构建脚本没有写 {name}/"
        assert f'runtime.join("{name}")' in sidecar, f"外壳没有找 {name}/"


def test_the_interpreter_path_matches_what_the_shell_invokes():
    """python-build-standalone puts the binary at bin/python3 (python.exe on Windows)."""
    sidecar = (
        Path(__file__).parent.parent / "desktop" / "src-tauri" / "src" / "sidecar.rs"
    ).read_text("utf-8")
    assert '"bin/python3"' in sidecar
    assert '"python.exe"' in sidecar
