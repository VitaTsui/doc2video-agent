"""The numbers that decide what comes out, as things a person may change.

Every one of these was measured rather than chosen — the pause before an
emphasised sentence is 0.55 because 0.42 was indistinguishable from `say`'s
own 0.41, the speech-rate ceiling is 340 because a real deck sits near 290.
That makes them good defaults and not laws: a deck of dense charts wants
longer pauses than a keynote, and whose video it is decides that, not us.

So the default stays in the module that uses it, with the paragraph explaining
where it came from, and this only holds the override. Nothing is changed by
being listed here; a knob nobody has touched reads back exactly the constant.

Bounds exist for the same reason the defaults do: a zoom of 40× is not a
preference, it is a broken video, and a form that accepts it is a form that
produces one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, get_settings
from .logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Knob:
    """One number: what it is called, what it does, and how far it may go."""

    id: str
    name: str
    what: str
    default: float
    low: float
    high: float
    # How to write it: 「秒」「字/分」「×」「%」. Display only.
    unit: str = ""
    # Whole numbers where a fraction would be meaningless (a character count).
    integer: bool = False


def _knobs(settings: Settings | None = None) -> dict[str, Knob]:
    """Built on demand, so importing this does not import half the pipeline.

    Takes the settings it is asked about rather than the process-wide ones:
    two of these knobs default to a setting, and reading the global copy made
    a caller's own configuration invisible to them.
    """
    from ..skills import director, render_review, speech_review
    from ..tools.tts import units

    settings = settings or get_settings()

    listed = [
        Knob(
            "voice.lead",
            "每页开口前留白",
            "页面先到，说话后到；转场期间开口，讲的是还没看见的东西",
            settings.scene_lead_seconds,
            0.0,
            2.0,
            "秒",
        ),
        Knob(
            "voice.tail",
            "每页收尾后留白",
            "最后一个字落地，再翻页",
            settings.scene_tail_seconds,
            0.0,
            2.0,
            "秒",
        ),
        Knob(
            "voice.pause_sentence",
            "句号后停",
            "引擎自己的停顿之外再加的",
            units.PAUSE_SENTENCE,
            0.0,
            1.5,
            "秒",
        ),
        Knob(
            "voice.pause_comma",
            "逗号后停",
            "句子内部的换气",
            units.PAUSE_COMMA,
            0.0,
            1.0,
            "秒",
        ),
        Knob(
            "voice.pause_enum",
            "顿号后停",
            "并列的词之间，最短的一档",
            units.PAUSE_ENUM,
            0.0,
            0.8,
            "秒",
        ),
        Knob(
            "voice.pause_exclaim",
            "问号感叹号后停",
            "话落地了，再停一下",
            units.PAUSE_EXCLAIM,
            0.0,
            2.0,
            "秒",
        ),
        Knob(
            "voice.pause_semicolon",
            "分号后停",
            "一个意思的两半，停但不断",
            units.PAUSE_SEMICOLON,
            0.0,
            1.5,
            "秒",
        ),
        Knob(
            "voice.pause_colon",
            "冒号后停",
            "话还没说完，往下引",
            units.PAUSE_COLON,
            0.0,
            1.5,
            "秒",
        ),
        Knob(
            "voice.pause_ellipsis",
            "省略号后停",
            "话没说尽，停久一点",
            units.PAUSE_ELLIPSIS,
            0.0,
            2.0,
            "秒",
        ),
        Knob(
            "voice.pause_dash",
            "破折号后停",
            "插一句补充",
            units.PAUSE_DASH,
            0.0,
            1.5,
            "秒",
        ),
        Knob(
            "voice.pause_emphasis",
            "重点句前停",
            "写稿时标了重点的那一句",
            units.PAUSE_EMPHASIS,
            0.0,
            2.0,
            "秒",
        ),
        Knob(
            "voice.pause_turn",
            "转折前停",
            "「不过」「但是」这类词之前",
            units.PAUSE_TURN,
            0.0,
            2.0,
            "秒",
        ),
        Knob(
            "shot.max_zoom_chars",
            "值得推近的字数",
            "整段正文推近没有意义",
            float(director.MAX_ZOOM_CHARS),
            10,
            200,
            "字",
            integer=True,
        ),
        Knob(
            "shot.max_scale",
            "最大放大倍数",
            "再大就糊了",
            director.RENDER_MAX_SCALE,
            1.2,
            6.0,
            "×",
        ),
        Knob(
            "shot.min_result",
            "推近的最小收益",
            "放大不到这个幅度就不推",
            director.MIN_ZOOM_RESULT,
            0.0,
            0.5,
            "",
        ),
        Knob(
            "review.too_fast",
            "语速上限",
            "再快听的人跟不上",
            speech_review.TOO_FAST,
            200.0,
            600.0,
            "字/分",
        ),
        Knob(
            "review.too_slow",
            "语速下限",
            "再慢画面在等旁白",
            speech_review.TOO_SLOW,
            60.0,
            300.0,
            "字/分",
        ),
        Knob(
            "review.monotone_seconds",
            "多久要有个停顿",
            "一直不停，念出来就是平的",
            speech_review.MONOTONE_SECONDS,
            4.0,
            40.0,
            "秒",
        ),
        Knob(
            "review.still_enough",
            "画面算「没动」",
            "抽帧比出来的差值，低于它算静止",
            render_review.STILL_ENOUGH,
            1.0,
            30.0,
            "",
        ),
        Knob(
            "review.action_change",
            "动作要看得见",
            "低于这个变化就是没画出来",
            render_review.ACTION_MIN_CHANGE,
            1.0,
            40.0,
            "",
        ),
    ]
    # The beats inside a sentence are ours to set only for an engine that
    # cannot find them itself. A neural voice is handed the whole page and
    # phrases it, so every one of these ten does nothing — and a row of numbers
    # that changes nothing is worse than no row at all.
    if _engine_paces_itself(settings):
        listed = [knob for knob in listed if not knob.id.startswith("voice.pause_")]
    return {knob.id: knob for knob in listed}


def _engine_paces_itself(settings: Settings) -> bool:
    """Whether the voice this machine speaks in decides its own beats.

    The chosen voice lives in the preferences file, not in settings — settings
    are the environment the process started with and are frozen for its life,
    and picking a voice does not restart anything. Reading only the settings
    made this answer 「no」 on a machine speaking with Edge, and the ten knobs
    stayed on screen doing nothing.
    """
    try:
        from ..tools.tts import TTSTool
        from . import prefs

        chosen = prefs.load(settings).voice or settings.tts_voice
        return bool(TTSTool(settings)._engine_for(chosen).paces_itself)
    except Exception:  # noqa: BLE001 - a settings page must still render
        return False


def knobs(settings: Settings | None = None) -> dict[str, Knob]:
    return _knobs(settings)


def value(knob_id: str, settings: Settings | None = None) -> float:
    """What this number is right now: the override if there is one, else the
    default the code was written with. Out-of-range values are clamped rather
    than refused — a stored file is not a form, and refusing here would mean
    failing a render over a number somebody typed weeks ago."""
    from . import prefs

    knob = _knobs(settings).get(knob_id)
    if knob is None:
        raise KeyError(knob_id)
    chosen = prefs.load(settings).rules.get(knob_id)
    if chosen is None:
        return knob.default
    return max(knob.low, min(knob.high, float(chosen)))


def report(settings: Settings | None = None) -> list[dict]:
    """Every knob, with what it is set to and what it was born as."""
    return [
        {
            "id": knob.id,
            "name": knob.name,
            "what": knob.what,
            "value": value(knob.id, settings),
            "default": knob.default,
            "low": knob.low,
            "high": knob.high,
            "unit": knob.unit,
            "integer": knob.integer,
        }
        for knob in _knobs(settings).values()
    ]
