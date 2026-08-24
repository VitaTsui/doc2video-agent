"""TTS tool: text in, audio file plus sentence timestamps out."""

from __future__ import annotations

import contextlib
from pathlib import Path

from ...core import ledger, telemetry
from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from . import align, phrasing
from .base import (
    Segment,
    TTSProvider,
    TTSResult,
    allocate_segments,
    estimate_duration,
    join_units,
    silences,
    weight_of,
)
from .edge import EdgeProvider
from .kokoro import KokoroProvider
from .pronounce import for_speech
from .providers import AUTO_ORDER, resolve_provider
from .units import plan_units

log = get_logger(__name__)

# How many times to measure a clause and cut it again. Cutting one silence can
# leave the engine free to take another somewhere else; three passes was enough
# to settle every clause measured on a 30-page deck.
REPHRASE_ROUNDS = 3


# Which of the built-in voices is which. macOS reports no gender, and asking
# for 「女声」 is how people ask — so the mapping is stated once, here, for the
# voices that ship with the system in Chinese.
VOICE_GENDER = {
    # Kokoro names its voices by language and gender: zf_* female, zm_* male.
    "zf_xiaobei": "female",
    "zf_xiaoni": "female",
    "zf_xiaoxiao": "female",
    "zf_xiaoyi": "female",
    "zm_yunjian": "male",
    "zm_yunxi": "male",
    "zm_yunxia": "male",
    "zm_yunyang": "male",
    "Tingting": "female",
    "Sinji": "female",
    "Meijia": "female",
    "Flo": "female",
    "Sandy": "female",
    "Shelley": "female",
    "Grandma": "female",
    # Piper, by model name: the one the runtime ships on Windows and Linux.
    "zh_CN-huayan-medium": "female",
    "zh_CN-huayan-x_low": "female",
    "Eddy": "male",
    "Reed": "male",
    "Rocko": "male",
    "Grandpa": "male",
}


def gender_of(voice: str) -> str | None:
    """Whether this voice is spoken of as male or female, if we know.

    Matched on the first word: macOS names its Mandarin voices
    「Flo (中文（中国大陆）)」, and the part that identifies the speaker is the
    part before the language. Keying the table on the full name meant 「换个
    女声」 matched exactly one voice out of nine.
    """
    head = voice.split("(")[0].strip()
    return VOICE_GENDER.get(voice) or VOICE_GENDER.get(head)


def voices_available(settings: Settings | None = None) -> list[str]:
    """The Chinese voices this machine can actually speak with.

    Asked of whichever provider is in use, because the answer differs in kind:
    a dozen built into macOS, one model file shipped with the runtime on
    Windows and Linux, none at all when the provider is silence. Empty is the
    honest answer for the last of those — a menu that changes nothing is worse
    than no menu.
    """
    settings = settings or get_settings()
    return resolve_provider(settings.tts_provider).voices()


class TTSTool:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._provider: TTSProvider = resolve_provider(self._settings.tts_provider)
        self._fallback: TTSProvider | None = None
        # The project's own dictionary, for the second pass over a line that
        # came out with a word cut in half.
        self._pronunciation: dict[str, str] = {}
        log.info("TTS provider: %s", self._provider.name)

    def _engine_for(self, voice: str) -> TTSProvider:
        """The engine that owns this voice, when a voice was named.

        A voice name says which engine it belongs to — `zh-CN-YunyangNeural` is
        Edge's, `zm_yunxi` is Kokoro's, `Tingting` is the system's. Reading it
        that way is what lets 「用播音腔讲」 work: the desktop app has no place
        to set an environment variable, and asking someone to set one to change
        a voice is asking them not to.
        """
        if not voice or voice in self._provider.voices():
            return self._provider
        for provider_cls in (EdgeProvider, KokoroProvider, *AUTO_ORDER):
            candidate = provider_cls()
            if voice in candidate.voices() and candidate.available():
                if candidate.name != self._provider.name:
                    log.info("按音色 %s 切到 %s", voice, candidate.name)
                return candidate
        return self._provider

    def _for_engine(self, text: str, pronunciation: dict[str, str] | None = None) -> str:
        """The words as the engine should receive them, before its own markers."""
        return for_speech(text, self._pronunciation if pronunciation is None else pronunciation)

    def _speak_unit(
        self, unit, work: Path, index: int, *, spoken, voice: str, rate: float
    ) -> list[tuple[Path, float]]:
        """Speak one clause, and hand back the clips it actually took.

        For engines with no markup of their own. One that can be asked to hold
        a beat goes through `_speak_sentences` instead, and never gets here.

        A clause has no punctuation inside it, so every silence in its clip is
        one nobody asked for — the engine deciding on its own where a phrase
        ends, and getting it wrong on any term it does not know: 「一是供应链经营
        风险可控化」 came back with 0.14 seconds after 一是供应.

        So the clip is measured, cut at the nearest word boundary to each
        silence, and each piece spoken on its own. Rejoined with their quiet
        edges trimmed, the silence is gone rather than moved — two pieces run
        *shorter* than the original (2.47s against 2.51s), while telling the
        engine where the boundary is costs 19% more length, because `say`
        re-plans its prosody around a marker.

        Repeated until the pieces come back clean, because cutting one silence
        can leave the engine free to invent another somewhere else.
        """
        pieces = [unit.text]
        for _round in range(REPHRASE_ROUNDS):
            clips, again = [], False
            for order, piece in enumerate(pieces):
                clip = work / f"{index:02d}_{order}.wav"
                duration = self._speak_once(spoken(piece), clip, voice=voice, rate=rate)
                clips.append((piece, clip, duration))
            rewritten: list[str] = []
            for piece, clip, duration in clips:
                cuts = phrasing.cuts_for(piece, silences(clip), duration)
                parts = phrasing.split(piece, cuts) if cuts else [piece]
                if len(parts) > 1:
                    log.debug("断词修复：%s → %s", piece, " | ".join(parts))
                    again = True
                rewritten.extend(parts)
            if not again:
                return [
                    (clip, unit.pause_before if order == 0 else 0.0)
                    for order, (_piece, clip, _duration) in enumerate(clips)
                ]
            for _piece, clip, _duration in clips:
                clip.unlink(missing_ok=True)
            pieces = rewritten

        # Out of rounds: speak what we have and let the last measurement stand.
        out: list[tuple[Path, float]] = []
        for order, piece in enumerate(pieces):
            clip = work / f"{index:02d}_{order}.wav"
            self._speak_once(spoken(piece), clip, voice=voice, rate=rate)
            out.append((clip, unit.pause_before if order == 0 else 0.0))
        return out

    def _speak_sentences(
        self, units, work: Path, *, spoken, engine, voice: str, rate: float
    ) -> tuple[list[Path], list[float], list[int]]:
        """Speak one sentence per call, with its own beats written into it.

        The clause is the right unit to *design* a pause on and the wrong unit
        to synthesise: `say` gives every call a complete intonation — its own
        opening pitch, its own falling close — so a page cut into thirty calls
        is thirty utterances spliced together. Measured on a real film: 4930
        characters in 511 calls, ten characters each. What that sounds like is
        two people finishing each other's sentences, and it was reported as
        exactly that.

        So the sentence is spoken in one call and the beats inside it are
        asked for in the engine's own markup, which keeps one arc across the
        whole sentence. The beats *between* sentences stay exact silence
        written by `join_units` — those are the ones the writing asked for, and
        they are still ours.

        Costs about 5% in length (60.3s against 63.6s on a 288-character page):
        an engine renders its own marker a little longer than the arithmetic we
        would have written. Worth it.
        """
        clips: list[Path] = []
        pauses: list[float] = []
        owners: list[int] = []
        parts: list[str] = []
        sentence = units[0].sentence
        gap = units[0].pause_before

        def flush() -> None:
            if not parts:
                return
            clip = work / f"s{len(clips):03d}.wav"
            self._speak_once("".join(parts), clip, voice=voice, rate=rate)
            clips.append(clip)
            pauses.append(gap)
            owners.append(sentence)
            parts.clear()

        for unit in units:
            if unit.sentence != sentence:
                flush()
                sentence, gap = unit.sentence, unit.pause_before
            elif parts:
                parts.append(engine.pause_markup(unit.pause_before))
            parts.append(spoken(unit.text))
        flush()
        return clips, pauses, owners

    def _speak_once(self, text: str, out_path: Path, *, voice: str, rate: float) -> float:
        """One clip, with a local voice standing by.

        The chosen voice may live on someone else's machine. Losing the network
        at page nine of thirty should cost that page its intended voice, not
        cost the run — so the first failure switches to the best local voice
        and the rest of the deck is spoken by that one. Recorded as a
        degradation, because a video half in one voice and half in another is
        something the person who made it has to be told about.
        """
        self._provider = self._engine_for(voice)
        try:
            return self._provider.synthesize(text, out_path, voice=voice, rate=rate)
        except Exception as exc:  # noqa: BLE001 - any failure, one answer
            local = self._local_fallback()
            if local is None:
                raise
            telemetry.record_degradation(
                "配音", f"{self._provider.name} 合成失败，改用 {local.name}：{str(exc)[:120]}"
            )
            log.warning("%s 合成失败，改用 %s：%s", self._provider.name, local.name, exc)
            self._provider = local
            return local.synthesize(text, out_path, voice="", rate=local.natural_rate)

    def _local_fallback(self) -> TTSProvider | None:
        """The best voice on this machine, whatever the chosen one was."""
        if self._fallback is None:
            for provider_cls in AUTO_ORDER:
                if provider_cls.name == self._provider.name:
                    continue
                candidate = provider_cls()
                if candidate.available():
                    self._fallback = candidate
                    break
        return self._fallback

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def chars_per_second(self) -> float:
        """How fast this machine's chosen voice actually speaks Chinese."""
        return self._provider.chars_per_second

    @property
    def voice(self) -> str:
        return self._settings.tts_voice

    def engine_name(self, voice: str = "") -> str:
        """Which engine would speak this voice — asked without speaking.

        `provider_name` is whatever is loaded right now, and that changes the
        moment something is synthesised: a tool that has not spoken yet says
        `macos_say` and the same tool one clip later says `edge`. Anything that
        remembers the answer has to ask this instead, or it remembers two
        different answers for one unchanged project.
        """
        return self._engine_for(voice).name

    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        sentences: list[str] | None = None,
        emphasis: list[bool] | None = None,
        voice: str = "",
        rate: float = 0.0,
        pronunciation: dict[str, str] | None = None,
    ) -> TTSResult:
        """Speak ``text``, and say when each of its sentences happens.

        `voice` and `rate` override what the machine is configured with — a
        project chooses its own, and the machine's values are the default it
        starts from. `emphasis` is the writer's mark on the sentences that
        matter, and it decides where the beats go.
        """
        lines = sentences or [text]
        segments, duration, source = self._speak(
            lines,
            out_path,
            emphasis=emphasis,
            pronunciation=pronunciation,
            voice=voice or self._settings.tts_voice,
            # What was asked for, relative to what this engine calls normal.
            rate=self._provider.natural_rate * (rate or self._settings.tts_speech_rate or 1.0),
        )
        return TTSResult(
            path=out_path,
            duration=duration,
            provider=self._provider.name,
            voice=voice or self._settings.tts_voice,
            segments=segments,
            timing_source=source,
        )

    def _speak(
        self,
        sentences: list[str],
        out_path: Path,
        *,
        emphasis: list[bool] | None,
        pronunciation: dict[str, str] | None,
        voice: str,
        rate: float,
    ) -> tuple[list[Segment], float, str]:
        """Write the clip and report exactly when each sentence lands.

        A page spoken in one go comes back in one voice — the engine settles on
        an average pace and holds it, pausing the same length at every mark.
        Broken up, the beats are ours: longer before the sentence the writer
        marked, longer where the script turns.

        Where it is broken depends on what the engine can be told. One that
        holds on request is asked to, and speaks a sentence per call; one that
        cannot is given a clause per call and the silence is written between
        the clips. The first keeps a sentence in one intonation, which is what
        「有两个人在说话」 turned out to be about — see `_speak_sentences`.

        The timing follows for free. Every clip is measured as it is written,
        so every boundary is exact; only the sentences *inside* one clip still
        need the ladder.
        """
        # What is spoken, which is not always what is written: a caption reads
        # 「RAG 模块」 and a narrator says "R-A-G 模块". The segments keep the
        # written form, so the subtitles are untouched.
        # And a space in what comes out of the dictionary is not a space: it is
        # 「别在这里断开」, which each engine spells differently — see
        # `TTSProvider.phrase_boundary`.
        def spoken(text: str) -> str:
            return self._engine_for(voice).phrase_boundary(self._for_engine(text, pronunciation))

        self._pronunciation = pronunciation

        units = plan_units(sentences, emphasis=emphasis)
        # Which engine this will be, named before the first call rather than
        # after it. `_speak_once` switches the engine as it goes, so reading
        # `self._provider` here labelled the deck's first call with whatever
        # engine happened to be loaded before the voice was looked at — the
        # record said the first page was spoken by `macos_say` when every page
        # including that one was spoken by Edge.
        provider = self._engine_for(voice)
        engine = provider.name
        if len(units) <= 1:
            text = "".join(sentences)
            with ledger.call(f"tts:{engine}", f"{len(text)} 字"):
                duration = self._speak_once(spoken(text), out_path, voice=voice, rate=rate)
            segments, source = self._time(text, out_path, sentences, duration)
            return segments, duration, source

        work = out_path.parent / f".{out_path.stem}.units"
        work.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []
        pauses: list[float] = []
        owners: list[int] = []
        try:
            if provider.honours_phrase_boundary:
                # One call per sentence. The clause beats go inside it as the
                # engine's own markup; see `_speak_sentences`.
                with ledger.call(f"tts:{engine}", f"{len(''.join(sentences))} 字"):
                    clips, pauses, owners = self._speak_sentences(
                        units, work, spoken=spoken, engine=provider, voice=voice, rate=rate
                    )
            else:
                # No markup to put a beat in, so the clause is the call and the
                # silence is written between the clips.
                for index, unit in enumerate(units):
                    with ledger.call(f"tts:{engine}", f"{len(unit.text)} 字"):
                        spoken_clips = self._speak_unit(
                            unit, work, index, spoken=spoken, voice=voice, rate=rate
                        )
                    for clip, pause in spoken_clips:
                        clips.append(clip)
                        pauses.append(pause)
                        owners.append(unit.sentence)

            windows = join_units(clips, pauses, out_path)
            # A clause is what gets spoken; a sentence is what gets captioned and
            # what the camera is cut to. So a sentence's window is the first of
            # its clauses to the last — every boundary here was written by
            # `join_units` rather than estimated from the clip afterwards.
            spans: dict[int, list[float]] = {}
            for sentence, (start, end) in zip(owners, windows, strict=True):
                span = spans.setdefault(sentence, [start, end])
                span[0], span[1] = min(span[0], start), max(span[1], end)
            segments = [
                Segment(text=sentences[index], start=round(span[0], 3), end=round(span[1], 3))
                for index, span in sorted(spans.items())
                if index < len(sentences)
            ]
            duration = windows[-1][1] if windows else 0.0
            return segments, duration, "units"
        finally:
            for clip in clips:
                clip.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                work.rmdir()

    def _time(
        self, text: str, audio: Path, sentences: list[str], duration: float
    ) -> tuple[list[Segment], str]:
        """When each sentence starts, by the best means available.

        Three rungs, best first. The camera points at the moment a sentence
        begins, so the difference between them is the difference between the
        box appearing on the right phrase and appearing a second after it:

        1. the engine's own timings, when it reports any;
        2. the pauses measured in the clip that was just written;
        3. the clip's duration split in proportion to sentence length.

        The third is where this project started and it stays as the floor —
        a clip with no detectable pauses still has to be cut up somehow — but
        it is an estimate, and a run that falls back to it records that it did.
        """
        if reported := self._provider.timings(text, audio, duration):
            return reported, "provider"

        weights = [weight_of(line) for line in sentences]
        if measured := align.boundaries(audio, sentences, duration, weights):
            starts = [0.0, *measured]
            ends = [*measured, duration]
            return (
                [
                    Segment(text=line, start=round(start, 3), end=round(end, 3))
                    for line, start, end in zip(sentences, starts, ends, strict=True)
                ],
                "silence",
            )

        # Not a degradation worth a record per scene — it is the normal path on
        # a clip with no pauses (one short sentence), and thirty of them would
        # bury the records that matter. The source travels with the result.
        return allocate_segments(sentences, duration), "estimate"


__all__ = [
    "Segment",
    "TTSProvider",
    "TTSResult",
    "TTSTool",
    "allocate_segments",
    "estimate_duration",
]
