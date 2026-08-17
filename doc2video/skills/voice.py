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
from .base import Skill, SkillContext

log = get_logger(__name__)


class VoiceSkill(Skill):
    name = "presentation-voice"
    description = "TTS 配音，并给出句级时间戳"

    def __init__(self, ctx: SkillContext, tts: TTSTool | None = None) -> None:
        super().__init__(ctx)
        self.tts = tts or TTSTool(ctx.settings)

    def run(self, *, force: bool = False) -> None:
        audio_dir = self.ctx.store.audio_dir(self.project.project_id)
        synthesized = skipped = 0

        for scene in self.project.scenes:
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
            )

            scene.audio.path = self.ctx.store.relativize(self.project.project_id, result.path)
            scene.audio.duration = result.duration
            scene.audio.provider = result.provider
            scene.audio.voice = result.voice
            scene.audio.text_hash = fingerprint
            scene.duration = result.duration

            for segment, timed in zip(scene.segments, result.segments, strict=False):
                segment.start = timed.start
                segment.end = timed.end
            synthesized += 1

        self.log.info(
            "配音完成：新合成 %d 个场景，复用 %d 个，总时长 %.1f 秒",
            synthesized,
            skipped,
            self.project.total_duration(),
        )

    def _fingerprint(self, scene: Scene) -> str:
        payload = "|".join(
            [
                scene.narration,
                self.tts.provider_name,
                self.tts.voice,
                f"{self.ctx.settings.tts_speech_rate:.2f}",
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
