"""The writing budget has to add up to the duration that was asked for.

Nobody can fix a script that came out the wrong length: the audio is already
synthesised by then and its duration is authoritative. So the budget published
before anything is written is the only place the target duration is enforced.
"""

from __future__ import annotations

import pytest

from doc2video.core.config import Settings
from doc2video.schemas import DocumentPage, Source, SourceType, VideoProject
from doc2video.skills import NarrationSkill
from doc2video.skills.base import SkillContext
from doc2video.storage import ProjectStore


def _skill(settings: Settings, store: ProjectStore, *, pages: int, duration: float):
    project = VideoProject(
        project_id="proj_budget",
        source=Source(type=SourceType.PPTX, file="demo.pptx", path="source/demo.pptx"),
    )
    project.intent.duration = duration
    project.document.pages = [
        DocumentPage(index=i, title=f"第 {i} 页", width=1920, height=1080)
        for i in range(1, pages + 1)
    ]
    return NarrationSkill(SkillContext.build(project, store=store, settings=settings))


def test_speech_budget_leaves_room_for_the_pauses(settings: Settings, store: ProjectStore):
    """The silence at each end of a page is on screen but is not script."""
    skill = _skill(settings, store, pages=16, duration=480.0)
    guide = skill.guide()

    silence = settings.scene_lead_seconds + settings.scene_tail_seconds
    spoken = sum(row["target_seconds"] for row in guide)
    on_screen = sum(row["page_seconds"] for row in guide)

    assert spoken == pytest.approx(480.0 - silence * 16, abs=1.0)
    assert on_screen == pytest.approx(480.0, abs=1.0)


def test_page_seconds_is_speech_plus_its_own_silence(settings: Settings, store: ProjectStore):
    silence = settings.scene_lead_seconds + settings.scene_tail_seconds
    for row in _skill(settings, store, pages=6, duration=240.0).guide():
        assert row["page_seconds"] == pytest.approx(row["target_seconds"] + silence, abs=0.11)


def test_chars_are_budgeted_from_speech_not_screen_time(settings: Settings, store: ProjectStore):
    row = _skill(settings, store, pages=8, duration=300.0).guide()[0]
    assert row["target_chars"] == int(row["target_seconds"] * 4.6)


def test_a_short_target_still_leaves_something_to_say(settings: Settings, store: ProjectStore):
    """Silence for 20 pages exceeds a 25s request; the budget must not go negative."""
    guide = _skill(settings, store, pages=20, duration=25.0).guide()

    assert all(row["target_chars"] > 0 for row in guide)
    assert all(row["target_seconds"] > 0 for row in guide)
