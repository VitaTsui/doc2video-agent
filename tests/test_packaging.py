"""Guard against source that exists on disk but never reaches the repo.

`.gitignore` carries patterns for runtime directories (`storage/`, `out/`,
`tmp/`). An unanchored pattern matches at *every* level, so `storage/` silently
excluded the `doc2video/storage/` package — everything worked locally and the
published package was broken. A clean checkout catches it; so does this.
"""

from __future__ import annotations

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
