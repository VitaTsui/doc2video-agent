"""Locate ffmpeg / ffprobe, preferring a vendored binary over a system install.

Resolution order (first hit wins):

1. An explicit path from settings (``D2V_FFMPEG_PATH`` / ``D2V_FFPROBE_PATH``).
2. The system ``PATH``.
3. A binary vendored inside the Python environment, from either wheel below.

Vendoring means ``pip install 'doc2video-agent[bundled]'`` is enough to produce
an MP4 — no ``brew install`` step, and the same ffmpeg build on every machine.

Two wheels can supply one. ``ffmpeg-binaries`` is preferred: it ships ffprobe
alongside ffmpeg, and its build has ``drawtext`` on every platform it covers,
so subtitles survive. It publishes no Linux/arm64 wheel, and there
``imageio-ffmpeg`` takes over — a newer ffmpeg, but ffmpeg only, and its Linux
build has no ``drawtext``. Hence both stay optional and everything downstream
probes rather than assumes (see ``has_filter`` and ``probe_duration``).

Both are **GPL** builds; see README before shipping either in a closed-source
product.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..core.config import get_settings
from ..core.logging import get_logger

log = get_logger(__name__)

DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}\.\d+)")


@dataclass(frozen=True)
class Binary:
    name: str
    path: str | None
    source: str  # "configured" | "system" | "bundled" | "missing"

    @property
    def available(self) -> bool:
        return self.path is not None


def _from_ffmpeg_binaries(name: str) -> str | None:
    """``ffmpeg-binaries``: ships both binaries inside the wheel."""
    try:
        with warnings.catch_warnings():
            # It warns at import time when its wheel carries no binary for this
            # platform; falling through to the next source is the answer.
            warnings.simplefilter("ignore")
            import ffmpeg
    except ImportError:
        return None
    # Deliberately never ffmpeg.init(): that downloads binaries over the
    # network. The paths are already populated at import when the wheel has
    # them. The attribute check also rejects the unrelated `ffmpeg-python`
    # package, which claims the same import name.
    attribute = "FFMPEG_PATH" if name == "ffmpeg" else "FFPROBE_PATH"
    path = getattr(ffmpeg, attribute, None)
    return str(path) if path else None


def _from_imageio(name: str) -> str | None:
    """``imageio-ffmpeg``: ffmpeg only, but the only Linux/arm64 wheel."""
    if name != "ffmpeg":
        return None
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # wheel present but no binary for this platform
        log.debug("内置 ffmpeg 不可用：%s", exc)
        return None


def _vendored(name: str) -> str | None:
    for source in (_from_ffmpeg_binaries, _from_imageio):
        path = source(name)
        if path and Path(path).exists():
            return path
    return None


def _on_path(name: str) -> str | None:
    """A PATH hit, ignoring console-script shims from our own wheels.

    ``ffmpeg-binaries`` installs a Python launcher named ``ffmpeg`` into the
    environment's script directory. It shadows the real binary on PATH, would
    be reported as a *system* install, and pays an interpreter start-up on
    every one of the many ffmpeg calls a render makes. Search the rest of PATH
    instead, then let the vendored lookup find the actual binary.
    """
    scripts_dir = Path(sys.executable).parent
    entries = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and Path(entry) != scripts_dir
    ]
    return shutil.which(name, path=os.pathsep.join(entries))


@lru_cache(maxsize=2)
def resolve(name: str) -> Binary:
    """Resolve one media binary. Cached — the answer cannot change mid-process."""
    settings = get_settings()
    configured = settings.ffmpeg_path if name == "ffmpeg" else settings.ffprobe_path
    if configured and Path(configured).exists():
        return Binary(name, configured, "configured")

    system = _on_path(name)
    if system:
        return Binary(name, system, "system")

    bundled = _vendored(name)
    if bundled:
        return Binary(name, bundled, "bundled")

    return Binary(name, None, "missing")


def ffmpeg() -> Binary:
    return resolve("ffmpeg")


def ffprobe() -> Binary:
    return resolve("ffprobe")


def reset_cache() -> None:
    """Forget resolved paths — for tests that change the environment."""
    resolve.cache_clear()
    has_filter.cache_clear()


@lru_cache(maxsize=8)
def has_filter(name: str) -> bool:
    """Whether this ffmpeg build ships a given filter.

    Builds differ in what they compile in — the vendored Linux binary, for one,
    has no ``drawtext`` (no burned-in subtitles) while still having ``drawbox``
    and ``zoompan``. Asking beforehand lets a renderer drop one feature instead
    of failing the whole scene with "Filter not found".
    """
    binary = ffmpeg()
    if not binary.available:
        return False
    try:
        result = subprocess.run(
            [binary.path, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.SubprocessError:
        return False
    # Lines look like: " ... drawtext           V->V       Draw text on top of video"
    return any(f" {name} " in line for line in result.stdout.splitlines())


def probe_duration(path: Path) -> float | None:
    """Read a media file's duration, using whichever binary is available.

    ffprobe is the clean way, and is normally vendored now — but not on every
    platform, so keep parsing the ``Duration:`` line ffmpeg prints when asked
    to open a file.
    """
    probe = ffprobe()
    if probe.available:
        try:
            result = subprocess.run(
                [
                    probe.path, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return float(result.stdout.strip())
        except (subprocess.SubprocessError, ValueError):
            pass

    encoder = ffmpeg()
    if not encoder.available:
        return None
    try:
        # `-i` alone exits non-zero ("at least one output file must be specified")
        # but still prints the stream header we want, so do not check the code.
        result = subprocess.run(
            [encoder.path, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.SubprocessError:
        return None

    match = DURATION_RE.search(result.stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
