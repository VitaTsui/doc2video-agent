"""Logging setup. One place so API, CLI and workers log identically."""

from __future__ import annotations

import contextlib
import logging
import sys

_CONFIGURED = False


def use_utf8() -> None:
    """Make this process able to print its own messages.

    Every message this project writes — logs, `doctor`, progress, errors — is
    in Chinese, and a Windows console still defaults to a legacy code page. The
    result is not mangled text but a crash: `doctor` died on its first line with
    a UnicodeEncodeError, and so would every other command. Safe everywhere
    else, where the streams are already UTF-8 and this does nothing.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", "") or ""
        if encoding.lower().replace("-", "") != "utf8":
            with contextlib.suppress(AttributeError, ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    use_utf8()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # These are chatty and rarely useful during pipeline debugging.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
