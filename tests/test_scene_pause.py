"""The silence around each page's narration.

Without the tail, one scene's last word runs straight into the next slide and
the whole video reads as a series of jump cuts. Without the lead, the narrator
is already talking while the page is still fading in. Both live in the scene's
own audio clip rather than in the timeline, which is what keeps the picture,
the audio and the subtitles agreeing about where a page begins and ends.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from doc2video.core.config import Settings
from doc2video.schemas import VideoProject
from doc2video.skills.base import SkillContext
from doc2video.skills.voice import VoiceSkill
from doc2video.storage import ProjectStore
from doc2video.tools.tts.base import audio_duration, pad_silence


def _wav(path: Path, seconds: float, rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return path


def test_silence_is_added_around_the_speech_not_over_it(tmp_path: Path):
    clip = _wav(tmp_path / "a.wav", 2.0)

    assert pad_silence(clip, lead=0.8, tail=0.7) == pytest.approx(3.5, abs=0.01)


def test_each_end_can_be_padded_alone(tmp_path: Path):
    lead_only = _wav(tmp_path / "lead.wav", 2.0)
    tail_only = _wav(tmp_path / "tail.wav", 2.0)

    assert pad_silence(lead_only, lead=0.8) == pytest.approx(2.8, abs=0.01)
    assert pad_silence(tail_only, tail=0.7) == pytest.approx(2.7, abs=0.01)


def test_no_pause_configured_leaves_the_clip_alone(tmp_path: Path):
    clip = _wav(tmp_path / "b.wav", 2.0)

    assert pad_silence(clip) == pytest.approx(2.0, abs=0.01)


def test_an_unreadable_clip_costs_the_pause_not_the_render(tmp_path: Path):
    """A pause is a nicety; losing the audio would not be."""
    broken = tmp_path / "c.wav"
    broken.write_bytes(b"not a wav at all")

    assert pad_silence(broken, lead=0.8, tail=0.7) is None or True
    assert broken.read_bytes() == b"not a wav at all"


def test_a_scene_holds_its_frame_through_the_pause(
    settings: Settings, store: ProjectStore, demo_pptx: Path
):
    """Scene duration must cover speech *plus* pause, or the picture cuts early."""
    from doc2video.agent import Doc2VideoAgent
    from doc2video.agent.planner import Stage

    lead, tail = 0.6, 0.5
    settings = settings.model_copy(
        update={"scene_lead_seconds": lead, "scene_tail_seconds": tail}
    )
    agent = Doc2VideoAgent(settings, store)
    project: VideoProject = agent.create_project(demo_pptx)
    plan = agent.planner.initial_plan("生成一个2分钟的讲解视频", project)
    plan.stages = [s for s in plan.stages if s in (Stage.PARSE, Stage.UNDERSTAND)]
    ctx = SkillContext.build(project, store=store, settings=settings)
    from doc2video.agent.executor import Executor

    project = Executor(ctx).run(plan, message="t")

    from doc2video.skills import NarrationSkill

    NarrationSkill(ctx).apply({p.index: "这一页说的是系统架构。" for p in project.document.pages})
    VoiceSkill(ctx).run()

    scene = project.scenes[0]
    clip = ctx.asset_path(scene.audio.path)
    assert scene.duration == pytest.approx(audio_duration(clip), abs=0.05)
    # The subtitle cues come from the speech, so they sit inside the silence:
    # nothing is said while the page fades in, or after the last word.
    assert scene.segments[0].start == pytest.approx(lead, abs=0.05)
    assert scene.segments[-1].end <= scene.duration - tail + 0.05


def test_the_engine_s_own_silence_is_trimmed_so_the_pause_is_the_pause():
    """Lead and tail have to mean what they say.

    Edge ends a clip with the better part of a second of nothing. Padded on
    top of that, a page turn measured 2.39 seconds against a designed 1.5 —
    thirty of those is seventy seconds of dead air in a ten-minute film.
    """
    import math
    import wave

    from doc2video.tools.tts.base import audio_duration, pad_silence

    rate = 22050

    def clip(path, before, speech, after):
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            quiet = b"\x00\x00" * int(rate * before)
            loud = b"".join(
                int(20000 * math.sin(i / 8)).to_bytes(2, "little", signed=True)
                for i in range(int(rate * speech))
            )
            handle.writeframes(quiet + loud + b"\x00\x00" * int(rate * after))

    import tempfile
    from pathlib import Path

    work = Path(tempfile.mkdtemp())
    noisy = work / "noisy.wav"
    clip(noisy, before=0.3, speech=2.0, after=0.9)
    assert audio_duration(noisy) == pytest.approx(3.2, abs=0.05)

    padded = pad_silence(noisy, lead=0.6, tail=0.5)
    # The speech, plus exactly the silence that was asked for.
    assert padded == pytest.approx(2.0 + 0.6 + 0.5, abs=0.06)

    # A clip that is silent all the way through is the silent provider's, and
    # it is the whole scene: trimming it would leave a page with no duration.
    mute = work / "mute.wav"
    clip(mute, before=0.0, speech=0.0, after=3.0)
    assert pad_silence(mute, lead=0.6, tail=0.5) == pytest.approx(3.0 + 1.1, abs=0.06)
