"""presentation-director — narration semantics to visual attention.

The core of the system (方案 §10). The model decides *what should be looked at*;
this module decides *when*, deriving every timestamp from the TTS timeline
rather than from the model. That split is why the result is reproducible: the
same project renders to the same frames, and a re-voiced scene re-times its
actions automatically.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ..core import ledger, tuning
from ..schemas import (
    ActionType,
    BBox,
    DirectorAction,
    DocumentPage,
    ElementKind,
    NarrationSegment,
    PageType,
    Scene,
)
from .base import Skill

# Rhythm guardrails: too many camera moves is as bad as none (方案 §10 判断原则).
# A flat cap of four was one of them measured against nothing: on a page that
# is on screen for twenty-seven seconds and names five different boxes, the
# fifth sentence pointed at nothing and the film felt disjointed — the voice
# says 「再看这一块」 and the picture does not move. So the budget is time: one
# move per stretch of speech, floored so a short page still gets a gesture and
# capped so a long one does not turn into a slideshow of boxes.
# How much of an element's own text has to turn up in a sentence before the
# camera will point at it. Bigrams, so word order does not matter and a
# rephrasing still counts; 0.35 keeps a card apart from its neighbour on a
# four-card page, where the headings share their shape but not their nouns.
MENTION_THRESHOLD = 0.35

# Where a sentence changes what it is talking about.
_CLAUSE_SPLIT = re.compile(r"[，、；：]")

SECONDS_PER_ACTION = 6.0
MIN_ACTIONS_PER_SCENE = 2
MAX_ACTIONS_PER_SCENE = 8
MIN_ACTION_DURATION = 1.2
MAX_ACTION_DURATION = 4.0
# Below this an action flashes rather than reads; drop it instead of squeezing it.
MIN_KEEP_DURATION = 0.6
RESET_DURATION = 1.0
# A target covering more of the page than this is not worth zooming into.
MAX_ZOOM_COVERAGE = 0.35
# A pointer is a quick "look here" beat: it suits a passing mention of a small,
# precise target. Dwelling on something calls for a highlight or a zoom instead.
MAX_POINTER_COVERAGE = 0.10
MAX_POINTER_SPAN = 2.6
# A zoom says "look closely at this". Past this much text there is nothing to
# look closely at — a paragraph enlarged is still a paragraph, the narrator is
# not reading it, and the viewer gets a slow push into a wall of words. Found
# in a finished video: eleven of twenty-six zooms landed on blocks over forty
# characters, the largest of them 342. Those become outlines instead, which
# say "this block" without promising it is worth enlarging.
MAX_ZOOM_CHARS = 40
# The renderer will not push past this, so a target's size after zooming is
# known before the zoom is chosen. Kept in step with `MAX_SCALE` in
# `renderer/src/components/useCameraTransform.ts`; a test asserts they agree.
RENDER_MAX_SCALE = 3.0
# How much of the frame a target has to fill once the camera has done all it
# can. Below this the push buys nothing: the label is still too small to read
# and the page around it — which is what told the viewer where the label was —
# has been cropped away. Measured on a real deck: eight of twenty-six zooms
# landed on things that stayed under five percent of the frame at full scale.
MIN_ZOOM_RESULT = 0.05
POINTER_DURATION = 1.4
# Let the viewer hear the sentence start before the camera moves.
AUDIO_LEAD = 0.3
ZOOM_KINDS = {ElementKind.NUMBER, ElementKind.CHART, ElementKind.TABLE, ElementKind.IMAGE}

# Pages whose whole content is 「here is where we are」: a cover, a section
# divider. There is nothing on them to point at, and boxing something anyway is
# how a deck ended up with a highlight around the numeral 「1」 on a divider.
#
# A table of contents is not one of them, which cost it thirteen shots on a
# thirty-page deck. Its items are the one thing on the page worth pointing at:
# the narration walks the list — 「先立资质。再谈痛点和思路。」 — and the eye
# has nowhere to go. What the rule was really protecting against was the
# *heading* 「CONTENTS」, and `_is_banner` and `DECORATIVE_TEXT` already refuse
# that.
SIGNPOST_PAGES = {PageType.COVER, PageType.SECTION}

# Text with nothing in it worth looking at. A target has to carry information:
# a lone digit is the section's number, 「CONTENTS」 is the word above the list,
# and a box around either points the viewer at furniture.
DECORATIVE_TEXT = {
    "contents", "content", "agenda", "outline", "index", "目录", "大纲", "议程",
    "thank you", "thanks", "谢谢", "感谢", "汇报人", "报告人", "logo",
}
MIN_TARGET_CHARS = 2


class ActionChoice(BaseModel):
    segment_id: str
    type: ActionType
    target: str
    #: Where in the sentence this one is talked about, 0–1. A sentence can walk
    #: two items — 「第一部分是背景及技术牵头方，第二部分是核心市场痛点分析。」 —
    #: and one box for the pair sits on the wrong half of it for half the time.
    at_fraction: float = 0.0


class SceneDirection(BaseModel):
    actions: list[ActionChoice]


class DirectorSkill(Skill):
    name = "presentation-director"
    description = "把语言语义转换为视觉注意力和镜头动作"

    def run(self) -> None:
        document = self.project.document
        total_actions = 0

        for scene in self.project.scenes:
            page = document.page(scene.source_page) if scene.source_page else None
            if page is None:
                scene.actions = []
                continue
            # One entry per page, like every other stage that works page by
            # page. A single line for thirty pages of camera work says nothing
            # about the page whose box went to the wrong card.
            with ledger.call(
                "director", f"第 {scene.source_page} 页", covers=[ledger.scene_key(scene.scene_id)]
            ):
                choices = self._choose_heuristically(scene, page)
                scene.actions = self._to_actions(scene, page, choices)
            total_actions += len(scene.actions)

        self.log.info("镜头设计完成：共 %d 个动作", total_actions)

    # -- choosing what to look at ---------------------------------------
    def _choose_heuristically(self, scene: Scene, page: DocumentPage) -> list[ActionChoice]:
        """Rank segments by how much they deserve a camera move, then take the top few."""
        # A signpost page gets its transition and nothing else.
        if page.page_type in SIGNPOST_PAGES:
            return []

        scored: list[tuple[float, ActionChoice]] = []
        for segment in scene.segments:
            for target_id, fraction in self._targets_in(segment, page):
                element = page.element(target_id)
                if element is None or not _worth_pointing_at(element) or _is_banner(element, page):
                    continue
                score = element.importance + (0.5 if segment.emphasis else 0.0)
                # A node in a declared flow is worth more than a box that happens
                # to be on the page: the arrows say this one is part of the thing
                # being explained.
                if page.diagram is not None and target_id in page.diagram.nodes:
                    score += 0.3
                action_type = self._pick_action(segment, element, page)
                scored.append((
                    score,
                    ActionChoice(
                        segment_id=segment.id,
                        type=action_type,
                        target=target_id,
                        at_fraction=fraction,
                    ),
                ))

        scored.sort(key=lambda item: -item[0])
        chosen: list[ActionChoice] = []
        for _, choice in scored:
            chosen.append(choice)
            if len(chosen) >= _action_budget(scene):
                break
        # Keep narrative order — ranking was only for selection.
        order = {s.id: i for i, s in enumerate(scene.segments)}
        chosen.sort(key=lambda c: (order.get(c.segment_id, 0), c.at_fraction))
        # Never the same box twice *in a row* — that reads as a stutter. Twice
        # with something else in between is not a stutter but a return, and
        # refusing it left the picture still while the narration came back to
        # a box it had already left. (`_to_actions` enforces the same rule on
        # the timed actions; this keeps the count honest before the budget.)
        chosen = [
            choice
            for index, choice in enumerate(chosen)
            if index == 0 or choice.target != chosen[index - 1].target
        ]

        if sum(1 for c in chosen if c.type is ActionType.ZOOM) >= 2 and scene.segments:
            chosen.append(
                ActionChoice(segment_id=scene.segments[-1].id, type=ActionType.RESET, target="")
            )
        return chosen

    @staticmethod
    def _pick_action(segment: NarrationSegment, element, page: DocumentPage) -> ActionType:
        """Match the gesture to what the sentence is doing.

        Emphasis and data-bearing elements earn a zoom; a brief mention of a
        small element earns a pointer; anything else gets an outline. Without
        the pointer branch the director had only two gestures, so quick
        name-checks came out looking like the same emphasis as a key figure.
        """
        if segment.emphasis or element.kind in ZOOM_KINDS:
            # Unless it is a wall of text: a picture or a number rewards being
            # enlarged, a paragraph does not.
            wordy = element.kind not in ZOOM_KINDS and len(element.text or "") > tuning.value(
                "shot.max_zoom_chars"
            )
            if not wordy and _zoom_pays_off(element, page):
                return ActionType.ZOOM
            return ActionType.HIGHLIGHT
        span = max(segment.end - segment.start, 0.0)
        if span and span <= MAX_POINTER_SPAN and _coverage(element, page) <= MAX_POINTER_COVERAGE:
            return ActionType.POINTER
        return ActionType.HIGHLIGHT

    def _targets_in(
        self, segment: NarrationSegment, page: DocumentPage
    ) -> list[tuple[str, float]]:
        """Everything this sentence talks about, and roughly when it gets there.

        One box per sentence was wrong the moment a sentence covered two items:
        「第一部分是背景及技术牵头方，第二部分是核心市场痛点分析。」 got a single
        box, on whichever half matched better, and it sat there through the
        other half. The clause is what walks the page, so the clause is what the
        camera follows — timed by where it falls in the sentence, because the
        sentence's own start and end are measured.
        """
        pieces = [piece for piece in _CLAUSE_SPLIT.split(segment.text) if piece.strip()]
        if len(pieces) < 2:
            bound = self._resolve_target(segment, page)
            return [(bound, 0.0)] if bound else []

        found: list[tuple[str, float]] = []
        spent = 0
        for piece in pieces:
            fraction = spent / max(len(segment.text), 1)
            spent += len(piece)
            target = self._resolve_target(
                NarrationSegment(id=segment.id, text=piece, emphasis=segment.emphasis), page
            )
            if target and (not found or found[-1][0] != target):
                found.append((target, fraction))
        if not found:
            bound = self._resolve_target(segment, page)
            return [(bound, 0.0)] if bound else []
        return found

    def _resolve_target(self, segment: NarrationSegment, page: DocumentPage) -> str | None:
        for ref in segment.element_refs:
            element = page.element(ref)
            if element is None or not _worth_pointing_at(element):
                continue
            if not _is_banner(element, page):
                return ref
        # Nothing bound: find the element this sentence is talking about.
        #
        # The model fills `element_refs` when it feels like it — measured on a
        # 30-page deck, 30 of 70 sentences came back with none, and seven pages
        # had not one — so the camera cannot depend on them. It used to fall
        # back on the element's first six characters appearing verbatim in the
        # sentence, which is a coin flip: the script says 「供应链经营风险可控化」
        # for a card headed 「01 供应链经营风险可控化」 and the six characters
        # matched, but 「持续监控原料的供需和价格」 for one headed 「实现原料供需、
        # 价格持续监控」 shares every word and not the first six.
        #
        # The script reads the page now — measured at 73–85% character overlap
        # — so asking how much of an element's own text turns up in the sentence
        # is both cheap and decisive.
        best: tuple[float, str] | None = None
        for element in page.elements:
            if element.kind is ElementKind.TITLE or not _worth_pointing_at(element):
                continue
            share = _mentioned(element.text, segment.text)
            if share < MENTION_THRESHOLD:
                continue
            score = share + element.importance / 10
            if best is None or score > best[0]:
                best = (score, element.id)
        return best[1] if best else None

    @staticmethod
    def _fit_action_type(choice: ActionChoice, page: DocumentPage) -> ActionType:
        """Downgrade a zoom whose target already fills most of the page.

        Zooming to a box that covers half the slide magnifies everything and
        singles out nothing — an outline communicates the same thing without
        throwing away context.
        """
        if choice.type not in (ActionType.ZOOM, ActionType.POINTER) or not choice.target:
            return choice.type
        element = page.element(choice.target)
        if element is None or not page.width or not page.height:
            return choice.type
        coverage = _coverage_box(focus_box(element, page), page)
        if choice.type is ActionType.POINTER:
            # Pointing at a box that fills the page indicates nothing.
            return ActionType.HIGHLIGHT if coverage > MAX_POINTER_COVERAGE else ActionType.POINTER
        return ActionType.HIGHLIGHT if coverage > MAX_ZOOM_COVERAGE else ActionType.ZOOM

    # -- turning choices into timed actions -------------------------------
    def _to_actions(
        self, scene: Scene, page: DocumentPage, choices: list[ActionChoice]
    ) -> list[DirectorAction]:
        segments = {s.id: s for s in scene.segments}
        actions: list[DirectorAction] = []

        # Page entry: every scene opens with a transition so cuts never feel abrupt.
        actions.append(
            DirectorAction(
                at=0.0,
                type=ActionType.TRANSITION,
                effect="fade" if page.page_type is not PageType.COVER else "fade-slow",
                duration=0.5,
            )
        )

        last_target: str | None = None
        for choice in choices:
            segment = segments.get(choice.segment_id)
            if segment is None:
                continue

            if choice.type is ActionType.RESET:
                # Reset belongs at the tail of the scene, returning to the full page.
                duration = min(RESET_DURATION, max(0.4, scene.duration * 0.15))
                at = max(0.0, scene.duration - duration)
            else:
                if choice.target and choice.target == last_target:
                    continue
                span = max(segment.end - segment.start, 0.0)
                if choice.type is ActionType.POINTER:
                    duration = min(POINTER_DURATION, max(MIN_KEEP_DURATION, span - 0.2))
                else:
                    duration = max(MIN_ACTION_DURATION, min(MAX_ACTION_DURATION, span - 0.2))
                at = max(0.0, segment.start + span * choice.at_fraction + AUDIO_LEAD)

            # An action must fit inside its own scene, and be long enough to read.
            duration = min(duration, scene.duration - at)
            if at >= scene.duration or duration < MIN_KEEP_DURATION:
                continue
            last_target = choice.target or last_target

            action_type = self._fit_action_type(choice, page)
            effect = {
                ActionType.ZOOM: "zoom-highlight",
                ActionType.HIGHLIGHT: "outline",
                ActionType.POINTER: "pointer",
                ActionType.RESET: "ease-out",
            }.get(action_type, "")

            actions.append(
                DirectorAction(
                    at=round(at, 2),
                    type=action_type,
                    target=choice.target or None,
                    effect=effect,
                    duration=round(duration, 2),
                    params={"segment_id": choice.segment_id},
                )
            )

        actions.sort(key=lambda a: a.at)
        return actions


# How far apart two things can be and still be one thing, as a fraction of the
# page's height. A caption sits just under its figure; anything further down is
# the next block.
GROUP_GAP = 0.03
# Past this the group has stopped being a part of the page and become the page.
# On a dense diagram slide the only thing that spans a label is the whole
# diagram, and framing 44% of the slide points at nothing — better to leave
# the label alone and let the rest of the director decide it is not a target.
MAX_GROUP_COVERAGE = 0.30
# The caption may be a little wider than dead-centre under its figure, and a
# figure may be a hair narrower than the text under it. Neither means they are
# unrelated.
GROUP_SLACK = 0.02


def focus_box(element, page: DocumentPage) -> BBox:
    """The box to draw around this element — usually the block it belongs to.

    A slide's parts arrive as separate elements because that is how they are
    stored, not because that is how they are read: a picture card and the two
    lines of caption under it are one thing to look at, and framing the second
    line alone — 「国家重点研发计划项目」, twenty characters at the bottom of a
    slide — points at the label instead of at what it labels.

    Growth is vertical and one-directional on purpose: a thing is absorbed only
    when it *spans* the box horizontally, which is what a figure does to its
    caption and a card does to its contents. Growing sideways as well would
    have swallowed the identical card beside this one — 「这一个」 turned into
    「这两个」, which is a different sentence.
    """
    box = element.bbox
    if not page.width or not page.height:
        return box

    gap = page.height * GROUP_GAP
    slack = page.width * GROUP_SLACK
    others = [
        other
        for other in page.elements
        if other.id != element.id
        and other.bbox.w > 0
        and not _is_banner(other, page)
        and not _is_backdrop(other, page)
    ]

    # One at a time, smallest first, so the box grows as far as it can rather
    # than as far as its largest neighbour would take it. Taking every match at
    # once was all-or-nothing: one oversized candidate in the round and the
    # element kept its own box, caption and figure included.
    for _ in range(len(others)):
        fits = [
            grown
            for other in others
            if _belongs(box, other.bbox, gap=gap, slack=slack)
            # Something already inside the box unions to the box itself, and
            # that is the smallest candidate there is — taken, it would win
            # every round and the box would never grow at all.
            and (grown := _union(box, other.bbox)) != box
            and _coverage_box(grown, page) <= MAX_GROUP_COVERAGE
        ]
        if not fits:
            break
        box = min(fits, key=lambda grown: grown.w * grown.h)
    return box


def _belongs(box: BBox, near: BBox, *, gap: float, slack: float) -> bool:
    """Whether `near` is something `box` is part of.

    It has to span the box horizontally — that is what a figure does to its
    caption and a card to its contents — and either touch it or sit within a
    caption's distance above or below.
    """
    spans = near.x <= box.x + slack and near.x + near.w >= box.x + box.w - slack
    above = 0 <= box.y - (near.y + near.h) <= gap
    below = 0 <= near.y - (box.y + box.h) <= gap
    overlaps = near.y < box.y + box.h and near.y + near.h > box.y
    return spans and (above or below or overlaps)


def _is_backdrop(element, page: DocumentPage) -> bool:
    """Whether this is the slide's decoration rather than one of its parts.

    Decks are built on artwork that bleeds off the edge: a wash in one corner,
    a band across the bottom. Such a shape contains half the things on the page
    without being what any of them belong to — the first version of this
    grouped a caption with the graphic behind it and framed the corner of the
    slide.

    A corner and no words is the test: two *adjacent* edges, or all four.
    Content keeps its margins; a picture that runs into the corner is there to
    be looked past. Opposite edges do not count — a chart drawn the full width
    of the slide touches left and right and is exactly the sort of thing a
    caption underneath belongs to.
    """
    if (element.text or "").strip():
        return False
    box = element.bbox
    edge_x, edge_y = page.width * 0.01, page.height * 0.01
    left = box.x <= edge_x
    top = box.y <= edge_y
    right = box.x + box.w >= page.width - edge_x
    bottom = box.y + box.h >= page.height - edge_y
    return (left and top) or (top and right) or (right and bottom) or (bottom and left)


def _union(a: BBox, b: BBox) -> BBox:
    x, y = min(a.x, b.x), min(a.y, b.y)
    return BBox(
        x=x, y=y, w=max(a.x + a.w, b.x + b.w) - x, h=max(a.y + a.h, b.y + b.h) - y
    )


def _mentioned(element_text: str, sentence: str) -> float:
    """How much of `element_text` turns up in `sentence`, by character bigram."""
    element_text, sentence = element_text.strip(), sentence.strip()
    if len(element_text) < 3 or len(sentence) < 3:
        return 0.0
    grams = {element_text[i : i + 2] for i in range(len(element_text) - 1)}
    said = {sentence[i : i + 2] for i in range(len(sentence) - 1)}
    return len(grams & said) / len(grams)


def _action_budget(scene: Scene) -> int:
    """How many camera moves this page can carry.

    One every six seconds was the rule, and it is the wrong question now that
    the script walks a page item by item: a 19-second contents page names five
    sections and got three boxes, so two of them were read out with the camera
    sitting on somebody else. What actually limits this is how long a box has
    to stay up to be read — anything more often than that is a flicker.
    """
    room = int(scene.duration // (MIN_ACTION_DURATION + AUDIO_LEAD))
    return max(MIN_ACTIONS_PER_SCENE, min(MAX_ACTIONS_PER_SCENE, room))


def _zoom_pays_off(element, page: DocumentPage) -> bool:
    """Whether pushing in on this actually shows the viewer anything.

    A zoom trades context for size: the page around the target is what said
    where the target was, and the camera crops it away. That trade is only
    worth making if the target ends up big enough to be worth looking at, and
    for a small label it never does — at the renderer's largest push a box
    covering two thousandths of the page still covers under two percent of the
    frame. The viewer loses the page and gains nothing.
    """
    # Judged on what will actually be framed. A caption on its own never pays
    # off; the figure it belongs to usually does, and that is what gets shown.
    coverage = _coverage_box(focus_box(element, page), page)
    return coverage * tuning.value("shot.max_scale") ** 2 >= tuning.value("shot.min_result")


def _is_banner(element, page: DocumentPage) -> bool:
    """Whether this element is the page's own heading.

    Pointing at it says nothing. The viewer is already on the page, and its
    title is what the whole page is about — a box drawn around it reads as the
    camera having nowhere better to go. Found in a finished video: three shots
    aimed at the page title, two of them at text identical to it.

    The fallback branch of `_resolve_target` had always skipped titles; the
    branch that takes the model's own `element_refs` did not, so a heading the
    model happened to bind to went straight through.
    """
    if element.kind is ElementKind.TITLE:
        return True
    text = (element.text or "").strip()
    return bool(text) and text == (page.title or "").strip()


def _worth_pointing_at(element) -> bool:
    """Whether there is anything on this element worth a viewer's eye.

    Non-text elements — a chart, an image, a table — are always worth it; they
    are the picture. Text has to say something: a lone digit or a stock heading
    is page furniture, and a box drawn around furniture reads as a mistake even
    when the sentence it belongs to is right.
    """
    if element.kind in ZOOM_KINDS and element.kind is not ElementKind.NUMBER:
        return True
    text = (element.text or "").strip()
    if len(text) < MIN_TARGET_CHARS:
        return False
    if text.lower().strip(" .、·:：") in DECORATIVE_TEXT:
        return False
    # Digits and punctuation only: a section number, a page number, a bullet.
    return any(ch.isalpha() or "\u4e00" <= ch <= "\u9fa5" for ch in text)


def _coverage(element, page: DocumentPage) -> float:
    """Share of the page area a element occupies, 0..1."""
    return _coverage_box(element.bbox, page)


def _coverage_box(box: BBox, page: DocumentPage) -> float:
    if not page.width or not page.height:
        return 1.0
    return (box.w * box.h) / (page.width * page.height)
