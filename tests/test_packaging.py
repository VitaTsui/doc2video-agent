"""Guard against source that exists on disk but never reaches the repo.

`.gitignore` carries patterns for runtime directories (`storage/`, `out/`,
`tmp/`). An unanchored pattern matches at *every* level, so `storage/` silently
excluded the `doc2video/storage/` package — everything worked locally and the
published package was broken. A clean checkout catches it; so does this.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "doc2video"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture(scope="module")
def tracked_files() -> set[str]:
    if not (PROJECT_ROOT / ".git").exists():
        pytest.skip("不在 git 工作区中")
    return set(_git("ls-files").splitlines())


def test_every_python_source_file_is_tracked(tracked_files: set[str]):
    on_disk = {
        str(path.relative_to(PROJECT_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    missing = sorted(on_disk - tracked_files)
    assert not missing, f"这些源码文件没有进仓库（很可能被 .gitignore 吞掉）：{missing}"


def test_every_prompt_and_schema_is_tracked(tracked_files: set[str]):
    assets = {
        str(path.relative_to(PROJECT_ROOT))
        for pattern in ("prompts/*.md", "schemas/json/*.json")
        for path in PACKAGE_ROOT.glob(pattern)
    }
    assert assets, "提示词与 JSON Schema 不应为空"
    assert not sorted(assets - tracked_files)


def test_every_subpackage_is_importable():
    """A directory with __init__.py must actually import as a module."""
    import importlib

    for init in sorted(PACKAGE_ROOT.rglob("__init__.py")):
        if "__pycache__" in init.parts:
            continue
        module = ".".join(init.parent.relative_to(PROJECT_ROOT).parts)
        importlib.import_module(module)


def test_runtime_directories_stay_ignored():
    """The fix must not go the other way and start committing project data."""
    # Trailing slashes matter: without them git treats a not-yet-created path as
    # a file, and a directory-only pattern such as `/tmp/` never matches — which
    # made this pass locally and fail on a fresh CI checkout.
    paths = ["storage/", "tmp/", "renderer/out/"]
    result = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert set(paths) <= set(result.stdout.split())


def test_every_surface_reports_the_same_version():
    """Four hard-coded copies is how the API came to report 0.3.0 while the
    project was on 0.6.0 — nothing fails when they disagree."""
    import tomllib
    from pathlib import Path

    from doc2video.api.app import create_app
    from doc2video.core import version
    from doc2video.mcp_server import build_server

    root = Path(__file__).parent.parent
    declared = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]

    assert version() == declared
    assert create_app().version == declared
    assert build_server().version == declared


def test_an_unsigned_package_is_left_out_of_the_update_manifest(tmp_path):
    """Listing it would be worse than omitting it.

    The app trusts one key and nothing else. A platform listed without a valid
    signature sends it to download a package it is bound to reject — the user
    sees "更新失败" where they should have seen nothing at all.
    """
    import sys

    art = tmp_path / "artifacts"
    # Laid out the way download-artifact leaves them: one directory per build
    # job. macOS names its updater bundle without a version or an architecture,
    # which is why the platform comes from the directory and not the filename.
    mac = art / "desktop-macos-arm64"
    linux = art / "desktop-linux-x64"
    mac.mkdir(parents=True)
    linux.mkdir(parents=True)
    (mac / "Doc2Video.app.tar.gz").touch()
    (mac / "Doc2Video.app.tar.gz.sig").write_text("dW50cnVzdGVk", encoding="utf-8")
    (linux / "doc2video_9.9.9_amd64.AppImage").touch()  # built without the key

    out = tmp_path / "latest.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "updater_manifest.py"
    built = "macos-arm64,linux-x64,windows-x64"
    done = subprocess.run(
        [sys.executable, str(script), str(art), "9.9.9", str(out), built],
        check=True,
        capture_output=True,
        text=True,
    )

    # Windows was in the matrix and produced nothing; that has to be visible on
    # the run, not buried. Intel macOS was never in it, so it must stay quiet —
    # a warning that fires every time is one nobody reads.
    assert "::warning::" in done.stdout
    assert "windows-x64" in done.stdout
    assert "::warning::darwin-x86_64" not in done.stdout

    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert list(manifest["platforms"]) == ["darwin-aarch64"]
    assert manifest["platforms"]["darwin-aarch64"]["url"].endswith(
        "/v9.9.9/Doc2Video.app.tar.gz"
    )


def test_the_updater_trusts_exactly_the_key_the_release_signs_with():
    """A public key in the config is the whole of the update's security.

    Without it any host answering the endpoint could install anything; with a
    stale one every update is rejected and the app quietly stops updating. It
    is the sort of value that only breaks in production, so it is pinned here.
    """
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "desktop/src-tauri/tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    updater = config["plugins"]["updater"]
    assert updater["pubkey"].strip(), "没有公钥，自更新等于没有校验"
    assert config["bundle"]["createUpdaterArtifacts"] is True, "不产出可更新的包，公钥也没用"
    assert updater["endpoints"] == [
        "https://github.com/VitaTsui/doc2video-agent/releases/latest/download/latest.json"
    ]


def test_the_shell_asks_for_the_base_the_build_script_would_produce():
    """These two must never drift.

    The Rust side embeds a digest and fetches `runtime-base-<digest>`; the
    build script computes the same digest from the dependencies and publishes
    under it. Let them disagree and the shell asks for a base nobody built —
    an install that cannot succeed, on every platform at once, discovered by
    the first person to click the button.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from build_runtime import base_version

    recorded = (PROJECT_ROOT / "desktop/src-tauri/base_version.txt").read_text().strip()
    assert recorded == base_version(), (
        f"base_version.txt 是 {recorded}，而依赖算出来是 {base_version()}。"
        "改过依赖之后要跑 scripts/build_runtime.py --print-base-version 重写它。"
    )


def test_the_base_digest_moves_when_a_dependency_does():
    """Otherwise it is decoration, and the split it guards is unsafe.

    A base pinned to a digest that does not react to `pyproject.toml` would let
    an app that needs a new package install into a tree without it — and that
    fails at the first render, not at install time.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import build_runtime

    before = build_runtime.base_version()
    original = build_runtime._dependency_block
    try:
        build_runtime._dependency_block = lambda: original() + '\n"some-new-package>=1.0",'
        assert build_runtime.base_version() != before
    finally:
        build_runtime._dependency_block = original
    assert build_runtime.base_version() == before
