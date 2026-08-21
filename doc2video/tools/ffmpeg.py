"""FFmpeg wrapper — encode, concat, mux.

Kept to primitives on purpose: filtergraph construction belongs to whichever
renderer adapter needs it, so that swapping renderers never means rewriting the
encoding layer (方案 §11).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.errors import DependencyMissing, ToolFailed
from ..core.logging import get_logger
from . import media_binaries

log = get_logger(__name__)

DEFAULT_TIMEOUT = 3600


def available() -> bool:
    return media_binaries.ffmpeg().available


def binary_path() -> str:
    binary = media_binaries.ffmpeg()
    if binary.path is None:
        raise DependencyMissing(
            "未检测到 ffmpeg。安装内置版本：pip install 'doc2video-agent[bundled]'；"
            "或安装系统版本：brew install ffmpeg",
            detail={"binary": "ffmpeg"},
        )
    return binary.path


def ensure_available() -> None:
    binary_path()


def run(args: list[str], *, timeout: int = DEFAULT_TIMEOUT) -> None:
    """Run ffmpeg with the given arguments, raising ToolFailed on non-zero exit."""
    cmd = [binary_path(), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise ToolFailed(
            "ffmpeg 执行失败",
            detail={"stderr": exc.stderr.decode("utf-8", "ignore")[-1500:], "args": args},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolFailed("ffmpeg 执行超时", detail={"args": args}) from exc


def concat(clips: list[Path], out_path: Path, *, work_dir: Path | None = None) -> Path:
    """Concatenate pre-rendered clips into one silent video.

    Silent on purpose. Each clip carries a copy of its own narration, encoded
    to AAC, and an AAC frame does not divide evenly into a clip's length — the
    encoder pads, so the file measures a few tens of milliseconds longer than
    the pictures in it. Concatenating with those tracks attached made the
    timeline the *audio's* length, and the pictures fell behind by that padding
    once per scene: measured at two seconds by scene 27 of a thirty-scene film,
    which is a highlight that appears while the next sentence is being spoken.

    Dropped from a copy rather than from the clips themselves: the panel plays
    each scene's own clip, and a silent preview would be a worse answer to
    「这一页出来对不对」 than a slightly slower assembly. The copies are stream
    copies — no re-encode, about a second for a thirty-scene film.
    """
    if not clips:
        raise ToolFailed("没有可拼接的片段")
    work_dir = work_dir or out_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    silent_dir = work_dir / "silent"
    silent_dir.mkdir(parents=True, exist_ok=True)
    silent: list[Path] = []
    for index, clip in enumerate(clips):
        copy = silent_dir / f"{index:04d}.mp4"
        run(["-i", str(clip), "-an", "-c:v", "copy", "-y", str(copy)])
        silent.append(copy)

    list_file = work_dir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{clip.resolve()}'" for clip in silent) + "\n", encoding="utf-8"
    )
    run(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c:v", "copy", str(out_path)])
    return out_path


def mux_audio(video: Path, audio: Path, out_path: Path) -> Path:
    """Attach an audio track to a finished video, trimming to the shorter stream."""
    run(
        [
            "-i", str(video),
            "-i", str(audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
    )
    return out_path


def concat_audio(clips: list[Path], out_path: Path, *, work_dir: Path | None = None) -> Path:
    """Concatenate narration clips into one continuous track."""
    if not clips:
        raise ToolFailed("没有可拼接的音频")
    work_dir = work_dir or out_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    list_file = work_dir / "concat_audio.txt"
    list_file.write_text(
        "\n".join(f"file '{clip.resolve()}'" for clip in clips) + "\n", encoding="utf-8"
    )
    run(
        [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ]
    )
    return out_path


def encode_still(
    image: Path,
    out_path: Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: int,
    video_filter: str | None = None,
) -> Path:
    """Encode a still image into a clip of the requested duration."""
    filters = video_filter or (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1"
    )
    run(
        [
            "-loop", "1",
            "-framerate", str(fps),
            "-t", f"{duration:.3f}",
            "-i", str(image),
            "-vf", filters,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            str(out_path),
        ]
    )
    return out_path
