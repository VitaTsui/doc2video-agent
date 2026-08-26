"""TTS tool: text in, audio file plus sentence timestamps out."""

from __future__ import annotations

import contextlib
from pathlib import Path

from ...core import ledger, telemetry
from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from . import align
from .base import (
    Segment,
    TTSProvider,
    TTSResult,
    allocate_segments,
    estimate_duration,
    join_units,
    retime_gaps,
    weight_of,
)
from .edge import EdgeProvider
from .kokoro import KokoroProvider
from .pronounce import for_speech
from .providers import AUTO_ORDER, SilentProvider, resolve_provider
from .units import plan_units

log = get_logger(__name__)

# How many times to measure a clause and cut it again. Cutting one silence can
# leave the engine free to take another somewhere else; three passes was enough
# to settle every clause measured on a 30-page deck.
#: How many times one clip is asked for before the engine is given up on. A
#: `say` timeout is a hung process, not a broken engine — and giving up cost a
#: whole deck its voice once.
SYNTHESIS_ATTEMPTS = 2


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

    def _for_engine(
        self,
        text: str,
        pronunciation: dict[str, str] | None = None,
        *,
        engine: TTSProvider | None = None,
    ) -> str:
        """The words as the engine should receive them, before its own markers."""
        speaking = engine or self._provider
        # An engine that needs none of our help gets none of it — not the two
        # lists, and not the deck's own dictionary either. 「完全交由引擎自己
        # 去。」 What it is handed is what the writer wrote.
        if speaking.reads_polyphones and speaking.spells_initialisms:
            return text
        return for_speech(
            text,
            self._pronunciation if pronunciation is None else pronunciation,
            reading=not speaking.reads_polyphones,
            letters=not speaking.spells_initialisms,
        )

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
        # Where the beats inside this sentence fall and how long they were
        # designed to be. The engine is asked for them and then held to them:
        # what it renders is its own idea of the length, about a tenth of a
        # second longer, and below 200ms it ignores the number entirely.
        marks: list[tuple[int, float]] = []
        said = 0
        sentence = units[0].sentence
        gap = units[0].pause_before

        def flush() -> None:
            nonlocal said
            if not parts:
                return
            clip = work / f"s{len(clips):03d}.wav"
            self._speak_once("".join(parts), clip, voice=voice, rate=rate)
            retime_gaps(clip, list(marks), said)
            clips.append(clip)
            pauses.append(gap)
            owners.append(sentence)
            parts.clear()
            marks.clear()
            said = 0

        for unit in units:
            if unit.sentence != sentence:
                flush()
                sentence, gap = unit.sentence, unit.pause_before
            elif parts and not unit.breath:
                marks.append((said, unit.pause_before))
                parts.append(engine.pause_markup(unit.pause_before))
            parts.append(spoken(unit.text))
            said += len(unit.text)
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

        Two things that switch is *not*. It is not a reason to give up on the
        engine after one bad clip: `say` timed out once on page four of a deck
        and the run went on to write twenty-seven pages of silence, because the
        fallback had been made the current engine and every later page started
        from it. So the engine is tried twice before anything is switched.

        And it is not permanent when what it falls back to is silence. Another
        voice is a voice, and staying on it keeps the film consistent; silence
        is the absence of one, and the next page deserves the real engine again.
        """
        engine = self._engine_for(voice)
        self._provider = engine
        last: Exception | None = None
        for attempt in range(SYNTHESIS_ATTEMPTS):
            try:
                return engine.synthesize(text, out_path, voice=voice, rate=rate)
            except Exception as exc:  # noqa: BLE001 - any failure, one answer
                last = exc
                if attempt + 1 < SYNTHESIS_ATTEMPTS:
                    log.warning("%s 合成失败，重试一次：%s", engine.name, exc)

        local = self._local_fallback()
        if local is None:
            raise last  # noqa: RSE102 - re-raise the engine's own failure
        telemetry.record_degradation(
            "配音", f"{engine.name} 合成失败，改用 {local.name}：{str(last)[:120]}"
        )
        log.warning("%s 合成失败，改用 %s：%s", engine.name, local.name, last)
        # Silence is for this clip only; another voice is for the rest of the run.
        if local.name != SilentProvider.name:
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
        chosen = voice or self._settings.tts_voice
        # Asked of the engine that will actually speak, not of whichever one
        # `self._provider` happens to hold. That field is only set once a clip
        # has been synthesised, and a deck is spoken eight scenes at a time:
        # all eight of the first batch read it before any of them had run, so
        # they got the *default* engine's idea of normal — 1.0 — while every
        # page after them got Edge's 0.86. One divided by 0.86 is sixteen
        # percent, and that is the step this leaves in the film: pages one to
        # eight spoken faster than the rest of it, in two separate runs, always
        # at exactly the width of the worker pool. 「前快后慢」.
        engine = self._engine_for(chosen)
        segments, duration, source = self._speak(
            lines,
            out_path,
            emphasis=emphasis,
            pronunciation=pronunciation,
            voice=chosen,
            # What was asked for, relative to what this engine calls normal.
            rate=engine.natural_rate * (rate or self._settings.tts_speech_rate or 1.0),
        )
        return TTSResult(
            path=out_path,
            duration=duration,
            provider=self._provider.name,
            voice=chosen,
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
        speaking = self._engine_for(voice)

        def spoken(text: str) -> str:
            return speaking.phrase_boundary(
                self._for_engine(text, pronunciation, engine=speaking)
            )

        self._pronunciation = pronunciation

        # Which engine this will be, named before the first call rather than
        # after it. `_speak_once` switches the engine as it goes, so reading
        # `self._provider` here labelled the deck's first call with whatever
        # engine happened to be loaded before the voice was looked at — the
        # record said the first page was spoken by `macos_say` when every page
        # including that one was spoken by Edge.
        provider = self._engine_for(voice)
        engine = provider.name
        # A page in one call, for an engine that phrases a page. Everything
        # below — a call per sentence, the silence we write between them, the
        # gaps cut back afterwards — exists to give an engine the phrasing it
        # cannot find for itself, and buys nothing from one that can. What it
        # costs is audible: the same page came back 11% shorter than the engine
        # would have said it, because our arithmetic had trimmed its breath.
        #
        # The timings still have to come from somewhere, and the ladder in
        # `_time` is where: this voice reports none, so the sentence boundaries
        # are the pauses measured in the clip that was just written.
        units = [] if provider.paces_itself else plan_units(sentences, emphasis=emphasis)
        if provider.paces_itself or len(units) <= 1:
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
            # One call per sentence, whatever the engine. Every call is a
            # complete intonation — its own opening pitch, its own falling
            # close — so a page cut into thirty calls is thirty utterances
            # spliced together, and what that sounds like is two people
            # finishing each other's sentences.
            #
            # This used to be done only for engines with pause markup of their
            # own, which meant only `say`. The desktop app's default voice is
            # Edge's, so the app never got the fix and still sounded like the
            # thing that was reported. The markup was never what made this
            # work: an engine that has none simply gets none, and the beats are
            # cut to their designed lengths afterwards either way.
            with ledger.call(f"tts:{engine}", f"{len(''.join(sentences))} 字"):
                clips, pauses, owners = self._speak_sentences(
                    units, work, spoken=spoken, engine=provider, voice=voice, rate=rate
                )

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
