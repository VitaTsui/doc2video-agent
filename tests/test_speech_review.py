"""How the finished audio sounds, measured off the clips that already exist.

The review beside this one reads the project and the one beside that looks at
the frames. Neither can hear: a page can have a good script, correct timings
and a caption in the right place, and be delivered faster than anyone follows.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from doc2video.schemas import Scene, SceneAudio, Source, SourceType, VideoProject
from doc2video.skills.speech_review import TOO_FAST, TOO_SLOW, check_speech

LEAD, TAIL = 0.8, 0.6


def _clip(path: Path, spans: list[tuple[float, bool]], rate: int = 22050) -> float:
    frames = bytearray()
    for seconds, speaking in spans:
        for index in range(int(seconds * rate)):
            frames += struct.pack("<h", int(9000 * math.sin(index * 0.05)) if speaking else 0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return sum(seconds for seconds, _ in spans)


def _project(tmp_path: Path, narration: str, spans: list[tuple[float, bool]]) -> VideoProject:
    duration = _clip(tmp_path / "scene_01.wav", spans)
    project = VideoProject(
        project_id="proj_speech",
        source=Source(type=SourceType.PPTX, file="d.pptx", path="source/d.pptx", page_count=1),
    )
    project.scenes = [
        Scene(
            scene_id="scene_01",
            source_page=1,
            narration=narration,
            duration=duration,
            audio=SceneAudio(path="scene_01.wav", duration=duration),
        )
    ]
    return project


def _check(project: VideoProject, tmp_path: Path):
    return check_speech(project, lambda rel: tmp_path / rel, lead=LEAD, tail=TAIL)


def test_a_page_read_too_fast_is_reported(tmp_path: Path):
    """Found on a real deck: three scenes over 350 characters a minute.

    They only showed up once the lead and tail silence came off the duration —
    counted in, every scene reads slower than it is spoken and the check
    quietly passes everything.
    """
    # 60 characters over 8 seconds of speech: 450 a minute.
    project = _project(tmp_path, "字" * 60, [(LEAD, False), (8.0, True), (TAIL, False)])
    findings = _check(project, tmp_path)

    assert [f.kind for f in findings] == ["speech_rate"]
    assert "偏快" in findings[0].message


def test_a_page_read_too_slowly_is_reported(tmp_path: Path):
    project = _project(tmp_path, "字" * 10, [(LEAD, False), (8.0, True), (TAIL, False)])
    findings = _check(project, tmp_path)

    assert [f.kind for f in findings] == ["speech_rate"]
    assert "偏慢" in findings[0].message


def test_an_ordinary_pace_is_left_alone(tmp_path: Path):
    """The engines this ships with land near 290 characters a minute."""
    project = _project(tmp_path, "字" * 40, [(LEAD, False), (8.0, True), (TAIL, False)])
    assert _check(project, tmp_path) == []
    assert TOO_SLOW < 290 < TOO_FAST


def test_a_long_stretch_with_no_pause_is_reported(tmp_path: Path):
    """The guard is about the engine, not the script.

    `say` breaks at every mark and never runs past five seconds, so this does
    not fire on anything shipping today. It is here because the next provider
    is exactly the kind of thing that would regress it, and a check added
    after the regression is a check that arrived late.
    """
    project = _project(tmp_path, "字" * 60, [(LEAD, False), (14.0, True), (TAIL, False)])
    kinds = [f.kind for f in _check(project, tmp_path)]
    assert "monotone" in kinds


def test_a_scene_too_short_to_judge_is_not_judged(tmp_path: Path):
    project = _project(tmp_path, "两个字", [(LEAD, False), (0.4, True), (TAIL, False)])
    assert _check(project, tmp_path) == []
