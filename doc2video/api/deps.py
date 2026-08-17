"""Shared singletons for the API process."""

from __future__ import annotations

from functools import lru_cache

from ..agent import Doc2VideoAgent, JobManager
from ..core.config import get_settings


@lru_cache(maxsize=1)
def get_agent() -> Doc2VideoAgent:
    return Doc2VideoAgent(get_settings())


@lru_cache(maxsize=1)
def get_jobs() -> JobManager:
    return JobManager(get_agent())
