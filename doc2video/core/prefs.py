"""What this machine was told to prefer, as opposed to what it was started with.

Settings come from the environment and are frozen for the life of the process
(`get_settings` is cached), which is right for everything an operator sets once
— ports, directories, binaries. It is wrong for a choice someone makes inside
the window: picking a voice should not mean restarting the backend, and a
choice that cannot be made without a restart is one people work around instead.

So this is the small, writable half: a file beside the projects, read fresh
every time. It holds only what a person changes by pointing at it. A missing
or damaged file is not an error — it means "nothing chosen yet", which is the
state every install starts in.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .config import Settings, get_settings
from .logging import get_logger

log = get_logger(__name__)

FILE = "preferences.json"


class Preferences(BaseModel):
    """Choices made in the window. Empty means "whatever the machine settles on"."""

    # The voice new videos start with. A video can still be told otherwise
    # (「用播音腔讲」), and that belongs to the video rather than to the machine.
    voice: str = ""


def path(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).storage_dir / FILE


def load(settings: Settings | None = None) -> Preferences:
    try:
        return Preferences.model_validate_json(path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable and never-written are the same answer, and neither is
        # worth failing a render over.
        return Preferences()


def save(prefs: Preferences, settings: Settings | None = None) -> Preferences:
    target = path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(prefs.model_dump(), ensure_ascii=False, indent=2), "utf-8")
    return prefs
