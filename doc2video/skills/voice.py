"""Voicing — TTS per scene, plus the timestamps everything downstream needs.

Scene duration becomes *authoritative* here: the estimate written during script
generation is replaced by the real clip length, and every segment gets a start
and end inside that clip. The director reads those timestamps; nothing else may
guess at timing.
"""

from __future__ import annotations

import hashlib

from ..core.logging import get_logger
from ..schemas import Scene
from ..tools.tts import TTSTool
from ..tools.tts.base import pad_silence
from .base import ProgressFn, Skill, SkillContext

log = get_logger(__name__)


class VoiceSkill(Skill):
    name = "presentation-voice"
    description = "TTS 配音，并给出句级时间戳"

    def __init__(self, ctx: SkillContext, tts: TTSTool | None = None) -> None:
        super().__init__(ctx)
        self.tts = tts or TTSTool(ctx.settings)

    def run(self, *, force: bool = False, progress: ProgressFn | None = None) -> None:
        audio_dir = self.ctx.store.audio_dir(self.project.project_id)
        synthesized = skipped = 0
        total = len(self.project.scenes)

        for done, scene in enumerate(self.project.scenes):
            if progress is not None:
                progress("voice", f"配音 {scene.scene_id}", done, total)
            fingerprint = self._fingerprint(scene)
            existing = self.ctx.asset_path(scene.audio.path)
            if (
                not force
                and scene.audio.text_hash == fingerprint
                and existing is not None
                and existing.exists()
            ):
                skipped += 1
                continue

            out_path = audio_dir / f"{scene.scene_id}.wav"
            result = self.tts.synthesize(
                scene.narration,
                out_path,
                sentences=[s.text for s in scene.segments] or [scene.narration],
                # The writer's own mark on the sentence that matters: it is
                # what decides where the beats go.
                emphasis=[s.emphasis for s in scene.segments],
                voice=self.project.intent.voice,
                rate=self.project.intent.speech_rate,
            )

            # Silence at both ends: the page arrives and settles before the
            # narrator starts, and the last word lands before the next slide.
            lead = self.ctx.settings.scene_lead_seconds
            padded = pad_silence(result.path, lead=lead, tail=self.ctx.settings.scene_tail_seconds)

            scene.audio.path = self.ctx.store.relativize(self.project.project_id, result.path)
            scene.audio.duration = padded or result.duration
            scene.audio.provider = result.provider
            scene.audio.voice = result.voice
            scene.audio.text_hash = fingerprint
            scene.duration = scene.audio.duration

            # Timestamps come back relative to the speech; the lead silence
            # pushes all of them later, and subtitles follow them exactly.
            for segment, timed in zip(scene.segments, result.segments, strict=False):
                segment.start = round(timed.start + lead, 3)
                segment.end = round(timed.end + lead, 3)
            synthesized += 1

        self.log.info(
            "配音完成：新合成 %d 个场景，复用 %d 个，总时长 %.1f 秒",
            synthesized,
            skipped,
            self.project.total_duration(),
        )

    def _fingerprint(self, scene: Scene) -> str:
        intent = self.project.intent
        payload = "|".join(
            [
                scene.narration,
                self.tts.provider_name,
                # The project's choice, not the machine's — otherwise asking
                # for a different voice leaves the fingerprint unchanged, the
                # existing clip is reused, and the change does nothing at all.
                intent.voice or self.tts.voice,
                f"{intent.speech_rate or self.ctx.settings.tts_speech_rate:.2f}",
                # Part of the clip, so changing either has to re-synthesise.
                f"{self.ctx.settings.scene_lead_seconds:.2f}",
                f"{self.ctx.settings.scene_tail_seconds:.2f}",
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
