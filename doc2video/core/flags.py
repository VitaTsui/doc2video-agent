"""Feature flags with percentage rollout — the 灰度 half of M4.

A flag names one risky choice that has two live implementations, and carries the
share of projects that should take the new path. The decision is a deterministic
hash of ``flag:project_id``, which matters more than it looks: a project that is
edited a week later must take the *same* arm it took the first time, or its
clips would be re-rendered by a different renderer and refuse to concatenate.

Every run records which arm it took (see ``core/telemetry.py``), so the cost and
quality numbers M4 collects are comparable per arm — that is what makes widening
a rollout a decision rather than a guess.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .config import Settings, get_settings
from .logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Flag:
    name: str
    description: str
    # Share of projects on the new path, 0–100. Overridable per deployment.
    default_percent: int


FLAGS: dict[str, Flag] = {
    "renderer_remotion": Flag(
        name="renderer_remotion",
        description="用 Remotion 渲染而不是纯 ffmpeg（镜头表现力更强，依赖 Node）",
        default_percent=100,
    ),
}


def rollout_percent(name: str, settings: Settings | None = None) -> int:
    """Configured share for a flag, clamped to 0–100.

    A non-integer share is rejected by Settings itself rather than defaulted
    here: a typo in a rollout config should fail loudly at startup, not quietly
    run at some other percentage than the operator believes.
    """
    settings = settings or get_settings()
    flag = FLAGS.get(name)
    if flag is None:
        raise KeyError(f"未知的特性开关：{name}")
    percent = settings.flags.get(name, flag.default_percent)
    return max(0, min(100, percent))


def enabled(name: str, key: str, settings: Settings | None = None) -> bool:
    """Whether ``key`` (a project id) falls inside this flag's rollout.

    Deterministic and stable for the life of the project — the same key always
    lands in the same bucket, so widening a rollout only ever adds projects.
    """
    percent = rollout_percent(name, settings)
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    return _bucket(name, key) < percent


def active_flags(key: str, settings: Settings | None = None) -> dict[str, bool]:
    """Every flag's decision for one key — recorded with the run."""
    return {name: enabled(name, key, settings) for name in FLAGS}


def report(settings: Settings | None = None) -> dict[str, dict[str, object]]:
    """Flag inventory for `doctor` and /health/capabilities."""
    return {
        name: {
            "percent": rollout_percent(name, settings),
            "description": flag.description,
        }
        for name, flag in FLAGS.items()
    }


def _bucket(name: str, key: str) -> int:
    digest = hashlib.sha256(f"{name}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) % 100
