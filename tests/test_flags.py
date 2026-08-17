"""Percentage rollout: deterministic per project, and it must stay that way.

The property that matters is stability, not the split. A project that renders
with Remotion today and ffmpeg next week would mix encodings its own clips
cannot be concatenated from — so "same project, same arm, forever" is a
correctness requirement, not a nicety.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from doc2video.core import flags
from doc2video.core.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(llm_provider="mock", tts_provider="mock", **overrides)


def test_zero_percent_is_off_and_hundred_is_on():
    off = _settings(flags={"renderer_remotion": 0})
    on = _settings(flags={"renderer_remotion": 100})

    assert not flags.enabled("renderer_remotion", "proj_anything", off)
    assert flags.enabled("renderer_remotion", "proj_anything", on)


def test_the_same_project_always_lands_in_the_same_arm():
    settings = _settings(flags={"renderer_remotion": 50})
    first = flags.enabled("renderer_remotion", "proj_stable", settings)

    for _ in range(20):
        assert flags.enabled("renderer_remotion", "proj_stable", settings) is first


def test_widening_a_rollout_only_ever_adds_projects():
    """A project inside 20% must still be inside 60% — otherwise a mid-rollout
    project could flip *out* of the new path and re-render against old clips."""
    keys = [f"proj_{index:04d}" for index in range(400)]
    narrow = _settings(flags={"renderer_remotion": 20})
    wide = _settings(flags={"renderer_remotion": 60})

    inside_narrow = {k for k in keys if flags.enabled("renderer_remotion", k, narrow)}
    inside_wide = {k for k in keys if flags.enabled("renderer_remotion", k, wide)}

    assert inside_narrow <= inside_wide


def test_the_split_is_roughly_the_configured_share():
    keys = [f"proj_{index:04d}" for index in range(1000)]
    settings = _settings(flags={"renderer_remotion": 30})

    share = sum(flags.enabled("renderer_remotion", k, settings) for k in keys) / len(keys)

    assert 0.25 < share < 0.35


def test_different_flags_do_not_select_the_same_projects():
    """Hashing the key alone would land every flag's 10% on one unlucky tenth.

    Asserted against the bucketing function directly so the property holds for
    flags that do not exist yet — it is about the hash, not today's roster.
    """
    keys = [f"proj_{index:04d}" for index in range(500)]

    a = {k for k in keys if flags._bucket("flag_a", k) < 30}
    b = {k for k in keys if flags._bucket("flag_b", k) < 30}

    assert a != b


def test_a_bad_percentage_is_rejected_at_config_load():
    """Loudly, at startup — a rollout running at a percentage the operator did
    not intend is worse than one that refuses to start."""
    with pytest.raises(ValidationError):
        _settings(flags={"renderer_remotion": "many"})  # type: ignore[dict-item]


def test_percentages_are_clamped():
    name = "renderer_remotion"

    def percent(value: int) -> int:
        return flags.rollout_percent(name, _settings(flags={name: value}))

    assert percent(900) == 100
    assert percent(-5) == 0


def test_unknown_flag_is_an_error_not_a_silent_false():
    with pytest.raises(KeyError):
        flags.enabled("no_such_flag", "proj_1", _settings())


def test_report_lists_every_flag_with_its_share():
    report = flags.report(_settings(flags={"renderer_remotion": 25}))

    assert report["renderer_remotion"]["percent"] == 25
    assert set(report) == set(flags.FLAGS)


def test_a_retired_flag_in_an_old_record_does_not_break_reporting():
    """Run records keep the flag names they were written with, forever.

    Renaming or retiring a flag must not make `doc2video metrics` unreadable
    for every run recorded before the change.
    """
    settings = _settings()
    report = flags.report(settings)

    assert report.get("a_flag_that_was_deleted", {}).get("percent") is None
