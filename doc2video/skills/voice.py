"""Voicing — TTS per scene, plus the timestamps everything downstream needs.

Scene duration becomes *authoritative* here: the estimate written during script
generation is replaced by the real clip length, and every segment gets a start
and end inside that clip. The director reads those timestamps; nothing else may
guess at timing.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context

from ..core import ledger, tuning
from ..core.logging import get_logger
from ..schemas import Scene
from ..tools.tts import TTSTool
from ..tools.tts.base import pad_silence
from .base import ProgressFn, Skill, SkillContext

log = get_logger(__name__)

# What counts as the wrong length for a clip, against what its own text
# estimates. Wide on purpose: a page whose words are mostly numbers or English
# speaks at a different pace, and 「重念一次」 costs a synthesis.
WRONG_LENGTH_LOW = 0.55
WRONG_LENGTH_HIGH = 1.9
# A run that keeps coming back wrong is not going to be fixed by asking again.
MAX_REDO = 5


#: Past this, speaking more pages at once buys nothing. Measured on a 30-page
#: deck: 1052s one at a time, 631s at four, 564s at eight, 560s at fourteen —
#: the engine is a system service that queues behind itself, so the machine's
#: core count is not the limit and using it only takes the machine away from
#: whoever is waiting for the film.
MAX_SPEAKERS = 8


def speaking_workers(count: int, configured: int) -> int:
    """How many pages to speak at once: what was asked for, bounded by measurement."""
    if count <= 1:
        return 1
    if configured > 0:
        return max(1, min(configured, count))
    return max(1, min(count, MAX_SPEAKERS, os.cpu_count() or 4))


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

        # What actually has to be spoken, once the unchanged pages are out.
        todo: list[tuple[Scene, str]] = []
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
            todo.append((scene, fingerprint))

        # Thirty pages spoken one after another was nine minutes of a forty
        # minute film, and no page waits on any other: each is its own text,
        # its own file, its own engine process.
        workers = speaking_workers(len(todo), self.ctx.settings.voice_workers)
        if workers > 1:
            self.log.info("配音 %d 页，%d 页一起念", len(todo), workers)
            synthesized += self._voice_together(todo, audio_dir, workers, progress, total)
        else:
            for done, (scene, fingerprint) in enumerate(todo):
                if progress is not None:
                    progress("voice", f"配音 {scene.scene_id}", done, total)
                self._voice_scene(scene, audio_dir, fingerprint)
                synthesized += 1

        self._check_and_redo(progress=progress)

        self.log.info(
            "配音完成：新合成 %d 个场景，复用 %d 个，总时长 %.1f 秒",
            synthesized,
            skipped,
            self.project.total_duration(),
        )

    def _voice_together(
        self,
        todo: list[tuple[Scene, str]],
        audio_dir,
        workers: int,
        progress: ProgressFn | None,
        total: int,
    ) -> int:
        """Speak several pages at once, reporting each as it lands.

        The account of a run is kept on a context variable, so work handed to a
        thread that does not carry the calling context records its calls
        nowhere — a parallel run would leave an empty ledger. Each task runs
        inside a copy of the context it was submitted from.
        """
        done = 0
        spoken = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for scene, fingerprint in todo:
                carried = copy_context()
                futures[
                    pool.submit(carried.run, self._voice_scene, scene, audio_dir, fingerprint)
                ] = scene
            for future in as_completed(futures):
                scene = futures[future]
                done += 1
                if progress is not None:
                    progress("voice", f"配音 {scene.scene_id}", done, total)
                try:
                    future.result()
                    spoken += 1
                except Exception:
                    # One page's failure is that page's. The rest of the deck is
                    # already in flight, and the review says what is missing.
                    self.log.exception("第 %s 页配音失败", scene.source_page)
        return spoken

    def _voice_scene(self, scene: Scene, audio_dir, fingerprint: str) -> None:
        """Speak one page, and write down when each of its sentences happens."""
        out_path = audio_dir / f"{scene.scene_id}.wav"
        # A page is spoken in several units, each its own call, made inside
        # the TTS tool — which knows about text and nothing about scenes.
        # This is where the scene is known, so this is where it is said.
        with ledger.scope(ledger.scene_key(scene.scene_id)):
            result = self.tts.synthesize(
                scene.narration,
                out_path,
                sentences=[s.text for s in scene.segments] or [scene.narration],
                # The writer's own mark on the sentence that matters: it is
                # what decides where the beats go.
                emphasis=[s.emphasis for s in scene.segments],
                pronunciation=self.project.intent.pronunciation,
                voice=self.project.intent.voice,
                rate=self.project.intent.speech_rate,
            )

        # Silence at both ends: the page arrives and settles before the
        # narrator starts, and the last word lands before the next slide.
        lead = tuning.value("voice.lead", self.ctx.settings)
        padded = pad_silence(
            result.path, lead=lead, tail=tuning.value("voice.tail", self.ctx.settings)
        )

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

    # -- 自查 -------------------------------------------------------------
    def _check_and_redo(self, *, progress: ProgressFn | None = None) -> None:
        """Listen to what came back, and say it again where it came back wrong.

        The same idea the script step uses: a deterministic rule, applied before
        the next step spends minutes on the result. Rendering is timed to this
        audio, so a clip that came back silent or half the length it should be
        costs a whole render to find out about.

        Two faults, both measurable without a model:

        * **Nothing there.** A file that is missing, empty, or has no sound in
          it at all — the silent provider's output looks exactly like a failed
          synthesis, and only the samples tell them apart.
        * **The wrong length.** Speech runs at a knowable pace; a clip half or
          double what its own text estimates is the engine having lost the
          plot, not a stylistic choice.
        """
        from ..tools.tts.base import estimate_duration, silences
        from ..tools.tts.providers import SilentProvider

        redone = 0
        for scene in self.project.scenes:
            path = self.ctx.asset_path(scene.audio.path)
            if path is None or not path.exists():
                continue
            spoken = scene.audio.duration - tuning.value(
                "voice.lead", self.ctx.settings
            ) - tuning.value("voice.tail", self.ctx.settings)
            expected = estimate_duration(
                scene.narration,
                self.project.intent.speech_rate or self.ctx.settings.tts_speech_rate,
                self.tts.chars_per_second,
            )
            # Re-speaking silence produces silence — but only when silence is
            # all this machine has. A page that came out silent while a real
            # voice is available is the one page that most needs saying again:
            # it is what a single `say` timeout looks like, and it cost a deck
            # twenty-seven mute pages before this told them apart.
            if scene.audio.provider == SilentProvider.name and not self._has_voice():
                continue

            quiet = sum(length for _, length in silences(path, floor=0.2))
            wrong = (
                spoken <= 0.2
                or quiet >= scene.audio.duration - 0.3
                or spoken > expected * WRONG_LENGTH_HIGH
                or spoken < expected * WRONG_LENGTH_LOW
            )
            if not wrong:
                continue
            self.log.info(
                "第 %s 页配音不对（%.1fs，估计 %.1fs，静音 %.1fs），重念一次",
                scene.source_page,
                spoken,
                expected,
                quiet,
            )
            if progress is not None:
                progress("voice", f"重念 {scene.scene_id}", 0, 0)
            with ledger.call("voice:redo", f"第 {scene.source_page} 页｜{spoken:.1f}s"):
                self._voice_scene(scene, self.ctx.store.audio_dir(self.project.project_id),
                                  self._fingerprint(scene))
            redone += 1
            if redone >= MAX_REDO:
                self.log.warning("重念次数已达上限 %d，剩下的保留原样", MAX_REDO)
                break

    def _has_voice(self) -> bool:
        """Is there anything on this machine that can actually speak?"""
        from ..tools.tts.providers import AUTO_ORDER, SilentProvider

        return any(
            cls.name != SilentProvider.name and cls().available() for cls in AUTO_ORDER
        )

    def _fingerprint(self, scene: Scene) -> str:
        intent = self.project.intent
        chosen = intent.voice or self.tts.voice
        payload = "|".join(
            [
                scene.narration,
                # The engine that *would* speak this voice, not the one loaded
                # at this instant. `provider_name` changes as soon as anything
                # is synthesised — a fresh tool says `macos_say`, the same tool
                # one clip later says `edge` — so a fingerprint taken from it
                # disagreed with itself between runs. The visible cost was one
                # page re-voiced and re-rendered on every unrelated edit: redo
                # page 5 and page 2 came back too, for nothing.
                self.tts.engine_name(chosen),
                # The project's choice, not the machine's — otherwise asking
                # for a different voice leaves the fingerprint unchanged, the
                # existing clip is reused, and the change does nothing at all.
                chosen,
                f"{intent.speech_rate or self.ctx.settings.tts_speech_rate:.2f}",
                # Part of the clip, so changing either has to re-synthesise.
                f'{tuning.value("voice.lead", self.ctx.settings):.2f}',
                f'{tuning.value("voice.tail", self.ctx.settings):.2f}',
                # How this deck says its own words. Without it, teaching the
                # machine a reading changed nothing at all: every clip matched
                # its fingerprint and was reused.
                #
                # Only the entries this page's words actually use, so teaching
                # 「宁波」 re-speaks the four pages that say it rather than the
                # whole film — and so a project that has taught nothing keeps
                # the fingerprints it already had.
                "|".join(
                    f"{term}={spoken}"
                    for term, spoken in sorted(intent.pronunciation.items())
                    if term in scene.narration
                ),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
