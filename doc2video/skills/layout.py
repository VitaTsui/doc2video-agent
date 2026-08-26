"""presentation-layout — subtitles, safe areas, and page-to-frame geometry.

Everything about "where things sit in the frame" lives here, so the renderer
adapters can stay geometry-free and the director can keep thinking in page
coordinates.
"""

from __future__ import annotations

import re
from math import ceil
from pathlib import Path

from ..schemas import BBox, DocumentPage, Scene, SubtitleCue

# One comfortable line of CJK subtitle at 1080p. Measured against a
# hand-written subtitle track for a deck of this kind: 78 captions, median 21
# characters, longest 32.
MAX_SUBTITLE_CHARS = 28
#: How far over that a clause may run before it is worth cutting. 28 is where
#: a line is comfortable, not where it stops being readable, and cutting a
#: 31-character clause to respect it cost more than the three characters were
#: worth: 「…中试基地制造」 / 「领域石化化工方向…」 — a cut that is a real word
#: boundary and still reads as a name that ended early. A clause within a
#: quarter of the target stays whole.
SUBTITLE_STRETCH = 1.25
MIN_CUE_SECONDS = 0.8

# Where a caption may break: the marks a speaker actually stops on. `、` and
# `：` are not among them — they separate items *inside* a clause, and cutting
# there produced captions of four characters. The same hand-written track
# keeps its `、` and never cuts at one.
CLAUSE_SPLIT = re.compile(r"(?<=[，,。！？!?；;…])")
PUNCTUATION = "，,。！？!?；;、：:…—－·「」『』（）()《》〈〉【】\"'“”‘’ "


def build_subtitles(scene: Scene, audio: Path | None = None) -> list[SubtitleCue]:
    """Split each narration segment into readable cues within its own window.

    `audio` is the scene's own clip, when there is one. Clauses are joined up
    to a line's worth, and whether two of them belong on one line depends on
    something only the clip knows: whether the voice stops between them. It
    usually does not — a comma costs about a third of a second — but a long
    enumeration can take a second and a half, and a caption held across that
    reads as one continuous sentence while the narrator audibly stops in the
    middle of it.
    """
    pauses = _pauses_in(audio)
    cues: list[SubtitleCue] = []
    for segment in scene.segments:
        span = max(segment.end - segment.start, 0.0)
        if span <= 0 or not segment.text.strip():
            continue
        chunks = _chunk(segment.text.strip())
        cursor = segment.start
        for chunk, share in zip(chunks, _shares(chunks, span), strict=True):
            cursor_end = min(cursor + share, segment.end)
            cues.append(
                SubtitleCue(
                    start=round(cursor, 3),
                    end=round(cursor_end, 3),
                    text=chunk,
                    scene_id=scene.scene_id,
                )
            )
            cursor = cursor_end
            if cursor >= segment.end:
                break
    return _split_where_the_voice_stops(cues, pauses)


def _split_where_the_voice_stops(
    cues: list[SubtitleCue], pauses: list[tuple[float, float]]
) -> list[SubtitleCue]:
    """Cut any caption the narrator breaks in the middle of.

    Clauses are joined up to a line's worth because a caption of seven
    characters is gone before it is read. Most joins are across a comma the
    voice runs through in a third of a second — but some are two thirds, and a
    line held across one of those reads as a single continuous sentence while
    the narrator audibly stops inside it.

    Done here rather than when the pieces are joined because here the cue has
    a time: there is no need to guess which comma a gap belongs to, only to
    ask whether this caption's own window contains one.
    """
    if not pauses:
        return cues

    out: list[SubtitleCue] = []
    for cue in cues:
        gap = next(
            (
                (at, length)
                for at, length in pauses
                # Both halves have to stand on their own, or the cure is a
                # caption that flashes past — which is the thing this whole
                # file was rearranged to stop doing.
                if length >= AUDIBLE_BREAK
                and cue.start + MIN_CUE_SECONDS < at < cue.end - MIN_CUE_SECONDS
            ),
            None,
        )
        if gap is None or " " not in cue.text:
            out.append(cue)
            continue

        at, _ = gap
        # Which join the gap falls at, by where it sits in the cue's window.
        spaces = [i for i, ch in enumerate(cue.text) if ch == " "]
        share = (at - cue.start) / max(cue.end - cue.start, 0.001)
        cut = min(spaces, key=lambda i: abs(i / len(cue.text) - share))
        out.append(
            SubtitleCue(
                start=cue.start, end=round(at, 3), text=cue.text[:cut], scene_id=cue.scene_id
            )
        )
        out.append(
            SubtitleCue(
                start=round(at, 3), end=cue.end, text=cue.text[cut + 1 :], scene_id=cue.scene_id
            )
        )
    return out


# A gap the ear registers as a stop. Below it a comma is just a comma; the
# measured median inside a sentence is 0.32s, and the ones worth splitting on
# are the tail — P90 1.37s, longest 1.53s.
AUDIBLE_BREAK = 0.45


def _pauses_in(audio: Path | None) -> list[tuple[float, float]]:
    """Every gap in this clip as (when, how long), in scene time."""
    if audio is None or not audio.exists():
        return []
    try:
        from ..tools.tts.align import find_pauses

        return [(pause.end - pause.duration / 2, pause.duration) for pause in find_pauses(audio)]
    except Exception:  # noqa: BLE001 - subtitles must not fail over a probe
        return []


def _shares(chunks: list[str], span: float) -> list[float]:
    """Divide a segment's window among its cues, longer cue → longer on screen.

    A floor keeps a two-character cue from flashing past, but it is capped at
    an equal split: cutting at every mark can leave more cues in a segment than
    ``MIN_CUE_SECONDS`` each would fit, and a floor that overruns the window
    would push the tail cues past ``segment.end``, where they are dropped.
    """
    total = sum(len(c) for c in chunks) or 1
    floor = min(MIN_CUE_SECONDS, span / len(chunks))
    raw = [max(span * len(c) / total, floor) for c in chunks]
    scale = span / sum(raw)
    return [share * scale for share in raw]


def _chunk(text: str) -> list[str]:
    """A caption per line's worth of speech, with the punctuation left off.

    A comma becomes a cut rather than a character — but not every comma, and
    that was the mistake. Cutting at every mark gave 270 captions over a
    six-minute film, a median of seven characters, two seconds each, and
    thirty-eight that were gone in under a second. A hand-written track for
    the same kind of deck runs 78 captions at a median of 21 characters and
    4.6 seconds; its captions hold a whole thought.

    So clauses are cut at the marks a speaker stops on, and then joined back
    up to a line's worth. Joining does not re-run the pause the audio just
    took — the line simply stays on screen across it, which is what reading a
    subtitle is like. The comma is replaced by a space, again as the reference
    track does.
    """
    pieces = [stripped for p in CLAUSE_SPLIT.split(text) if (stripped := p.strip(PUNCTUATION))]

    # A clause longer than a line still has to be broken somewhere. Into equal
    # parts rather than off the front: taking a full line at a time leaves the
    # tail as a remainder, and one nine-character caption after two long ones
    # reads as a mistake.
    split: list[str] = []
    # Which pieces begin at a punctuation mark, as opposed to in the middle of
    # a clause that was too long for one line. Only the first kind can line up
    # with a pause in the audio.
    marks: list[bool] = []
    for chunk in pieces:
        parts = (
            1
            if len(chunk) <= MAX_SUBTITLE_CHARS * SUBTITLE_STRETCH
            else ceil(len(chunk) / MAX_SUBTITLE_CHARS)
        )
        if parts <= 1:
            split.append(chunk)
            marks.append(True)
            continue
        for index, part in enumerate(_break_evenly(chunk, parts)):
            split.append(part)
            marks.append(index == 0)

    merged: list[str] = []
    for chunk, at_mark in zip(split, marks, strict=True):
        if merged and at_mark and len(merged[-1]) + 1 + len(chunk) <= MAX_SUBTITLE_CHARS:
            merged[-1] = f"{merged[-1]} {chunk}"
        else:
            merged.append(chunk)
    return merged or [text.strip(PUNCTUATION) or text]



def _break_evenly(text: str, parts: int) -> list[str]:
    """Cut a too-long clause into `parts`, never through a word.

    Chinese has no spaces, so a cut by character count lands wherever the
    arithmetic says: 「面向国家人工智能应用中试基地制造领域石化化工方向的应用场景
    揭榜」 is thirty-one characters, and cutting at twenty-eight put 「制造」 at
    the end of one caption and 「领域」 at the start of the next. A name broken
    across two captions reads as a different, shorter name — which is how
    「宁波城知产业链数据科技有限公司」 came out looking like it stopped at 「科技」.

    The same segmenter the speech side uses, for the same reason: it is the
    only thing here that knows where one word ends. Words are packed up to the
    target size and the cut falls between two of them. A single word longer
    than the target still has to be cut through — but there is no such word in
    a deck, and the arithmetic is the fallback rather than the rule.
    """
    from ..tools.tts.phrasing import words

    size = ceil(len(text) / parts)
    tokens = [word for word, _start, _end in words(text)] or list(text)
    out: list[str] = []
    current = ""
    for word in tokens:
        if current and len(current) + len(word) > size:
            out.append(current)
            current = word
        elif len(word) > size and not current:
            # Longer than a whole caption on its own: the only case where the
            # arithmetic has to win.
            for offset in range(0, len(word), size):
                out.append(word[offset : offset + size])
            current = out.pop() if out else ""
        else:
            current += word
    if current:
        out.append(current)
    return out or [text]


def to_frame_area(bbox: BBox, page: DocumentPage, width: int, height: int) -> BBox:
    """Map a page-pixel box to a 0..1 box in the rendered frame.

    The page is letterboxed into the frame, so the mapping has to account for
    the scale factor *and* the padding — otherwise every zoom target drifts.
    """
    page_w = page.width or float(width)
    page_h = page.height or float(height)
    scale = min(width / page_w, height / page_h)
    disp_w, disp_h = page_w * scale, page_h * scale
    off_x, off_y = (width - disp_w) / 2, (height - disp_h) / 2

    x = (off_x + bbox.x * scale) / width
    y = (off_y + bbox.y * scale) / height
    w = (bbox.w * scale) / width
    h = (bbox.h * scale) / height

    # Clamp into frame; a target partially off-screen would zoom to nowhere.
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.02, min(1.0 - x, w))
    h = max(0.02, min(1.0 - y, h))
    return BBox(x=x, y=y, w=w, h=h)


# How much air to leave around a highlight, in frame pixels.
#
# In pixels rather than as a ratio of the box, because this one is *drawn*: an
# outline padded by 8% of each dimension separately gets 16px of air beside a
# wide line of text and 2px above it, which is what made the box look loose on
# the sides and clamped on the glyphs. `BBox.padded` stays as it is — a zoom
# target genuinely does want a proportional margin.
HIGHLIGHT_PAD_PX = 7.0


def with_highlight_padding(area: BBox, width: int, height: int) -> BBox:
    """The same amount of air on every side of a drawn outline."""
    dx, dy = HIGHLIGHT_PAD_PX / width, HIGHLIGHT_PAD_PX / height
    x = max(0.0, area.x - dx)
    y = max(0.0, area.y - dy)
    return BBox(
        x=x,
        y=y,
        w=min(1.0 - x, area.w + 2 * dx),
        h=min(1.0 - y, area.h + 2 * dy),
    )
