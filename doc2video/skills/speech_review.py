"""How the finished audio sounds, measured rather than listened to.

The review beside this one reads the project and the one beside that looks at
the frames. Neither can hear. A page can have a good script, correct timings
and a caption in the right place, and still be delivered at a pace nobody can
follow — and the only way that gets noticed today is somebody playing the
video and saying "this sounds off".

Everything here comes off clips that already exist, using the pause detection
written for the timing ladder. Nothing is synthesised twice, and no model is
asked for an opinion: speaking rate is characters over seconds, and a stretch
with no pause in it is a stretch with no pause in it.

Thresholds are set against a real deck rather than from a style guide. On the
30-page one this project was built against, `say` delivers a median 287
characters a minute over a 263–371 range, and its longest unbroken stretch is
4.3 seconds.
"""

from __future__ import annotations

from pathlib import Path

from ..core import tuning
from ..schemas import ReviewFinding, VideoProject
from ..tools.tts.align import find_pauses

# Chinese narration lands near 290 characters a minute on the engines this
# ships with. Past 340 the listener is being outrun; below 180 the video is
# waiting for its own narrator.
TOO_FAST = 340.0
TOO_SLOW = 180.0
# A stretch of speech with no break in it. Twelve seconds is a long time to go
# without one — long enough that the delivery flattens whatever the words do.
#
# It does not fire on `say`, which breaks at every mark and never runs past
# five seconds. Kept because the guard is about the *engine*, not the script:
# the next provider is exactly the kind of thing that would regress here, and
# a check that only exists after the regression is a check that arrived late.
MONOTONE_SECONDS = 12.0
# Below this a scene is too short for either number to mean anything.
MIN_MEASURABLE = 2.0


def check_speech(
    project: VideoProject, asset_path, *, lead: float = 0.0, tail: float = 0.0, speed: float = 1.0
) -> list[ReviewFinding]:
    """Scenes delivered too fast, too slow, or without a breath.

    `lead` and `tail` are the silence wrapped around every clip. They have to
    come off before the rate means anything: counted in, a scene reads slower
    than it is spoken, and the fast one that prompted this check stops firing.

    `speed` is what the machine was told to speak at, and the band moves with
    it: 「偏快」 has to mean faster than asked for, not faster than the default
    somebody changed.
    """
    findings: list[ReviewFinding] = []
    for scene in project.scenes:
        clip = asset_path(scene.audio.path)
        if clip is None or not Path(clip).exists():
            continue
        spoken = scene.audio.duration - lead - tail
        if spoken < MIN_MEASURABLE or not scene.narration:
            continue

        rate = len(scene.narration) / spoken * 60
        # The band moves with the speed that was asked for. The numbers were
        # measured at the engine's own pace, and raising the default 5% put
        # ordinary pages over the line — eight 「偏快」 findings on a deck that
        # was speaking exactly as fast as it had been told to.
        asked = project.intent.speech_rate or speed or 1.0
        if rate > tuning.value("review.too_fast") * asked:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    kind="speech_rate",
                    scene_id=scene.scene_id,
                    message=f"语速 {rate:.0f} 字/分，偏快，听的人跟不上",
                )
            )
        elif rate < tuning.value("review.too_slow") * asked:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    kind="speech_rate",
                    scene_id=scene.scene_id,
                    message=f"语速 {rate:.0f} 字/分，偏慢，画面在等旁白",
                )
            )

        quiet = tuning.value("review.monotone_seconds")
        if (longest := _longest_unbroken(Path(clip), scene.audio.duration)) > quiet:
            findings.append(
                ReviewFinding(
                    severity="warning",
                    kind="monotone",
                    scene_id=scene.scene_id,
                    message=f"连续 {longest:.0f} 秒没有停顿，念起来是平的",
                )
            )
    return findings


def _longest_unbroken(clip: Path, duration: float) -> float:
    """The longest stretch of this clip with no pause in it."""
    marks = [0.0, *(pause.middle for pause in find_pauses(clip)), duration]
    gaps = [second - first for first, second in zip(marks, marks[1:], strict=False)]
    return max(gaps, default=0.0)
