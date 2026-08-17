"""What makes a rendered clip stale.

The incremental render reuses any clip whose fingerprint still matches, so a
change the fingerprint cannot see is a change that never reaches the screen —
the video stays correct only because these hold.
"""

from __future__ import annotations

from doc2video.tools.renderer.base import PlanAction, PlanSubtitle, ScenePlan


def _plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        duration=5.0,
        width=1920,
        height=1080,
        fps=30,
        image="/store/projects/p1/pages/page_01.png",
        audio="/store/projects/p1/audio/scene_01.wav",
        subtitles=[PlanSubtitle(start=0.0, end=2.0, text="第一句")],
    )


def test_fingerprint_is_stable_for_the_same_plan():
    assert _plan().fingerprint() == _plan().fingerprint()


def test_subtitle_text_is_part_of_the_fingerprint():
    """The bug this replaced: re-splitting subtitles left every scene "clean"."""
    before = _plan().fingerprint()
    plan = _plan()
    plan.subtitles[0].text = "第一句改过了"
    assert plan.fingerprint() != before


def test_subtitle_timing_is_part_of_the_fingerprint():
    before = _plan().fingerprint()
    plan = _plan()
    plan.subtitles[0].end = 2.5
    assert plan.fingerprint() != before


def test_actions_and_duration_are_part_of_the_fingerprint():
    before = _plan().fingerprint()
    plan = _plan()
    plan.actions.append(PlanAction(type="zoom", start=1.0, end=3.0))
    assert plan.fingerprint() != before

    longer = _plan()
    longer.duration = 5.5
    assert longer.fingerprint() != before


def test_subtitle_position_is_part_of_the_fingerprint():
    """Moving the caption has to re-burn it, not reuse the old position."""
    before = _plan().fingerprint()
    plan = _plan()
    plan.subtitle_margin = 0.2
    assert plan.fingerprint() != before


def test_moving_the_storage_directory_does_not_restage_a_render():
    """Same deck, same audio, different absolute path — still the same frames."""
    moved = _plan()
    moved.image = "/somewhere/else/projects/p1/pages/page_01.png"
    moved.audio = "/somewhere/else/projects/p1/audio/scene_01.wav"
    assert moved.fingerprint() == _plan().fingerprint()


def test_a_different_page_image_does_restage_a_render():
    moved = _plan()
    moved.image = "/store/projects/p1/pages/page_02.png"
    assert moved.fingerprint() != _plan().fingerprint()
