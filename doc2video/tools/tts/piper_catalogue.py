"""The published Piper voices, as something to look through and install.

Piper is the only pack here that is a *file format*: one voice is an ONNX
model plus its JSON config, and the provider already speaks with whatever is
in the voices directory. The missing half was everything before that — 174
voices exist, and finding one meant knowing that `rhasspy/piper-voices` is a
HuggingFace repository and reading a 240KB index by hand.

Fetched rather than bundled, because the list changes and a list shipped in a
build is a list that goes stale in the field. Cached on disk after the first
look so searching is instant and offline, and refreshed only when someone asks
— an index that re-downloads on every keystroke is worse than one that is a
week old.

Each file in the index carries its size and MD5, so an install can say what it
will cost before it starts and check what it got when it finishes. A truncated
model is a voice that fails at render time, which is the worst place to find
out about it.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

from ...core.config import Settings, get_settings
from ...core.errors import ToolFailed
from ...core.logging import get_logger

log = get_logger(__name__)

REPO = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
INDEX_URL = f"{REPO}/voices.json"
INDEX_FILE = "index.json"
# A week. The list grows by a few voices a year; the cost of being a week
# behind is nothing, and the cost of a fetch on every search is a search that
# does not work on a train.
INDEX_MAX_AGE = 7 * 24 * 3600
# The download is tens of megabytes over a link we do not control.
TIMEOUT = httpx.Timeout(30.0, read=300.0)


def voices_dir(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).storage_dir / "voices"


def _index_path(settings: Settings | None = None) -> Path:
    return voices_dir(settings) / INDEX_FILE


def index(settings: Settings | None = None, *, refresh: bool = False) -> dict:
    """The published list, from disk when it is fresh enough."""
    path = _index_path(settings)
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < INDEX_MAX_AGE:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("语音目录缓存损坏，重新下载")

    try:
        response = httpx.get(INDEX_URL, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001 - network, DNS, JSON
        # A stale copy beats no list at all: someone offline can still see
        # what they already have and what they looked at last time.
        if path.exists():
            log.warning("取不到最新语音目录，用缓存：%s", exc)
            return json.loads(path.read_text(encoding="utf-8"))
        raise ToolFailed("取不到 Piper 语音目录", detail={"error": str(exc)[:200]}) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _entry(key: str, voice: dict, installed: set[str]) -> dict:
    language = voice.get("language", {})
    model = next((name for name in voice.get("files", {}) if name.endswith(".onnx")), "")
    return {
        "key": key,
        "name": voice.get("name", key),
        "quality": voice.get("quality", ""),
        "language": language.get("code", ""),
        # Both, because neither alone is enough to recognise a language:
        # 「中文」 for the person who reads it and `Chinese` for the one who
        # knows the voice by its English name.
        "language_name": language.get("name_native", ""),
        "language_english": language.get("name_english", ""),
        "country": language.get("country_english", ""),
        "size": voice.get("files", {}).get(model, {}).get("size_bytes", 0),
        "installed": key in installed,
    }


def installed_keys(settings: Settings | None = None) -> set[str]:
    directory = voices_dir(settings)
    if not directory.is_dir():
        return set()
    return {path.stem for path in directory.glob("*.onnx")}


def search(query: str = "", limit: int = 40, settings: Settings | None = None) -> dict:
    """Voices matching `query`, installed ones first.

    Matched against everything someone might type: the key, the speaker's
    name, the language code, and the language in either language. 「中文」,
    `zh`, `Chinese` and `huayan` all have to find the same four voices.
    """
    settings = settings or get_settings()
    data = index(settings)
    here = installed_keys(settings)
    words = [word for word in query.lower().split() if word]

    rows = [_entry(key, voice, here) for key, voice in data.items()]

    def matches(row: dict) -> bool:
        haystack = " ".join(
            str(row[field]).lower()
            for field in ("key", "name", "language", "language_name", "language_english", "country")
        )
        return all(word in haystack for word in words)

    found = [row for row in rows if matches(row)]
    found.sort(key=lambda row: (not row["installed"], row["key"]))
    return {"total": len(rows), "matched": len(found), "voices": found[:limit]}


def install(key: str, settings: Settings | None = None) -> Path:
    """Download one voice, and check that what arrived is what was published.

    The model and its config, and nothing else — the repository also carries a
    MODEL_CARD per voice, which is for reading rather than for speaking.
    """
    settings = settings or get_settings()
    voice = index(settings).get(key)
    if voice is None:
        raise ToolFailed("没有这个音色", detail={"key": key})

    directory = voices_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    model: Path | None = None

    for remote, info in voice.get("files", {}).items():
        name = Path(remote).name
        if not name.endswith((".onnx", ".onnx.json")):
            continue
        target = directory / name
        _fetch(f"{REPO}/{remote}", target, info)
        if name.endswith(".onnx"):
            model = target

    if model is None:
        raise ToolFailed("这个音色没有模型文件", detail={"key": key})
    return model


def _fetch(url: str, target: Path, info: dict) -> None:
    log.info("正在下载 %s（%.1fMB）", target.name, info.get("size_bytes", 0) / 1024 / 1024)
    digest = hashlib.md5()  # noqa: S324 - integrity, not secrecy; it is what the index publishes
    try:
        with httpx.stream("GET", url, timeout=TIMEOUT, follow_redirects=True) as response:
            response.raise_for_status()
            # Written beside the target and moved at the end: a download that
            # dies halfway must not leave a file the provider will try to load.
            partial = target.with_suffix(target.suffix + ".part")
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 16):
                    digest.update(chunk)
                    handle.write(chunk)
    except Exception as exc:  # noqa: BLE001 - network, disk
        raise ToolFailed("下载失败", detail={"file": target.name, "error": str(exc)[:200]}) from exc

    expected = info.get("md5_digest", "")
    if expected and digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise ToolFailed("下载的文件校验不过", detail={"file": target.name})
    partial.replace(target)
