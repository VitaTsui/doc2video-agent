"""presentation-director — narration semantics to visual attention.

The core of the system (方案 §10). The model decides *what should be looked at*;
this module decides *when*, deriving every timestamp from the TTS timeline
rather than from the model. That split is why the result is reproducible: the
same project renders to the same frames, and a re-voiced scene re-times its
actions automatically.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

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
# …or this many character pairs outright. A clause can only ever cover a small
# share of a long paragraph — 「是聚焦基地建设任务」 shares 15% of the 57-character
# block it is quoting — so a share-only gate could never point at a paragraph
# and pointed at the four-character chip above it instead. Measured on that
# page: the right block shares 6–9 pairs with the clause quoting it, the wrong
# ones share 2–4.
MENTION_PAIRS = 6

# The pause between one marker leaving and the next arriving. Long enough that
# they are not on screen together, short enough not to read as nothing.
MARKER_GAP = 0.15

# A chip that names the block under it — 「揭榜要求」, 「基地介绍」 — is a label,
# not the thing being said. Boxing it puts a frame around four characters while
# the narrator reads the paragraph beneath, which is what it looked like from
# the sofa. Short *and* followed closely by something much longer: 「(一)背景及
# 技术牵头方」 on a contents page is equally short and has nothing under it, so
# it stays a target.
LABEL_CHARS = 8
LABEL_BODY_RATIO = 3.0
LABEL_GAP = 0.12

# When a page would otherwise get no camera at all, half a match is better than
# a motionless page.
LOOSE_MENTION = 0.2

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
    #: For a merged run, the moment the narration leaves this block. The box
    #: holds until then instead of expiring on a timer.
    holds_until: float = 0.0
    #: How well the clause that chose this actually matches it. Kept so that a
    #: block mentioned twice can be framed at the point it is *described*
    #: rather than at the point it is first brushed past.
    match: float = 0.0
    #: The other things this same clause names. One frame covers all of them:
    #: 「产业链结构，原料、产品、中间环节…」 is one thing being said, and a box
    #: around the four characters of its name points at the label instead of at
    #: what is being described.
    also: list[str] = Field(default_factory=list)


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
                scene.actions = self._check_and_redo(scene, page)
            total_actions += len(scene.actions)

        self.log.info("镜头设计完成：共 %d 个动作", total_actions)

    def _check_and_redo(self, scene: Scene, page: DocumentPage) -> list[DirectorAction]:
        """Look at what was chosen, and choose again where it does not hold.

        The same shape as the script step's own check: a deterministic rule,
        applied before the render spends minutes drawing the result.

        * **A box on something the sentence never mentions.** The camera is
          bound by what the sentence says, and a sentence that mentions nothing
          used to fall through to the best-scoring element on the page — a box
          on a card the narrator is not talking about, which is what 「框选跟
          讲稿对不上」 looks like.
        * **A page with nothing to look at.** A page that names things and gets
          no camera at all is a page the viewer watches sit still. Tried again
          with a looser bar before giving up: half a match is better than a
          motionless page, and 0.35 was measured on cards that share a shape.
        """
        segments = {segment.id: segment for segment in scene.segments}
        kept: list[DirectorAction] = []
        dropped = 0
        for action in scene.actions:
            segment_id = action.params.get("segment_id") if action.params else None
            segment = segments.get(segment_id or "")
            element = page.element(action.target) if action.target else None
            if action.target and element is not None and segment is not None:
                hit, share = _shared_grams(element.text, segment.text)
                if share < MENTION_THRESHOLD and hit < MENTION_PAIRS:
                    dropped += 1
                    continue
            kept.append(action)
        if dropped:
            self.log.info("第 %s 页去掉 %d 个讲稿没提到的镜头", scene.source_page, dropped)

        has_box = any(action.target for action in kept)
        if not has_box and _worth_looking_at(page):
            retry = self._choose_heuristically(scene, page, threshold=LOOSE_MENTION)
            if retry:
                self.log.info("第 %s 页原本没有镜头，放宽匹配后补上", scene.source_page)
                return self._to_actions(scene, page, retry)
        return kept

    # -- choosing what to look at ---------------------------------------
    def _choose_heuristically(
        self, scene: Scene, page: DocumentPage, *, threshold: float = MENTION_THRESHOLD
    ) -> list[ActionChoice]:
        """Rank segments by how much they deserve a camera move, then take the top few."""
        # A signpost page gets its transition and nothing else.
        if page.page_type in SIGNPOST_PAGES:
            return []

        scored: list[tuple[float, ActionChoice]] = []
        # Whether the writer did the binding step on this page at all. If it
        # did, an empty list is an answer — 「这一句在页面上没有出处」 — and the
        # camera should hold still rather than go looking. If it did not, the
        # empty lists mean nothing and everything has to be guessed.
        anyone_bound = any(segment.element_refs for segment in scene.segments)
        for segment in scene.segments:
            for target_id, also, fraction in self._targets_in(
                segment, page, threshold=threshold, bound_page=anyone_bound
            ):
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
                        match=_match(element.text, segment.text),
                        also=also,
                    ),
                ))

        # One gesture per page for the same kind of thing. A contents page whose
        # first four items got an outline and whose fifth got a red dot reads as
        # a mistake, not as emphasis: the items are siblings and the sentence
        # naming the last one is shorter only because it is last.
        kinds = {choice.type for _score, choice in scored}
        if ActionType.POINTER in kinds and kinds & {ActionType.HIGHLIGHT, ActionType.ZOOM}:
            scored = [
                (score, choice.model_copy(update={"type": ActionType.HIGHLIGHT})
                 if choice.type is ActionType.POINTER else choice)
                for score, choice in scored
            ]

        # 「框标题这种是禁止的。」 A box around a name and nothing it names points
        # at the label while the narrator describes what the label names —
        # 「再说招募目的。」 announces a section, and the sentence after it frames
        # what the section says. A pointer is exempt: it is a dot beside a
        # thing, not a box around it.
        #
        # After the normalisation above, not before. A heading is small and
        # briefly mentioned, so it is picked as a pointer — and then promoted
        # to a box by the rule that a page uses one gesture. Checked first,
        # every one of them slipped through as a pointer and came out a frame.
        scored = [
            (score, choice)
            for score, choice in scored
            if choice.type not in (ActionType.HIGHLIGHT, ActionType.ZOOM)
            or choice.also
            or not _just_a_name(page.element(choice.target), page)
        ]

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
        self,
        segment: NarrationSegment,
        page: DocumentPage,
        *,
        threshold: float = MENTION_THRESHOLD,
        bound_page: bool = False,
    ) -> list[tuple[str, list[str], float]]:
        """Everything this sentence talks about, and roughly when it gets there.

        One box per sentence was wrong the moment a sentence covered two items:
        「第一部分是背景及技术牵头方，第二部分是核心市场痛点分析。」 got a single
        box, on whichever half matched better, and it sat there through the
        other half. The clause is what walks the page, so the clause is what the
        camera follows — timed by where it falls in the sentence, because the
        sentence's own start and end are measured.
        """
        # What the writer said it was talking about. Authoritative when it is
        # there: it wrote the sentence with the page's element list in front of
        # it and knows which line it came from, while everything below this is
        # the camera reverse-engineering that answer out of shared characters.
        # All of them, not the first — 「落到供应链、外贸、招投标三类情报」 names
        # three chips and framing one of them points at a third of the sentence.
        bound = [
            ref
            for ref in segment.element_refs
            if (found := page.element(ref)) is not None
            and _worth_pointing_at(found)
            and not _is_banner(found, page)
        ]
        if bound:
            return [(bound[0], self._that_fit(bound[0], bound[1:], page), 0.0)]
        if bound_page:
            # This page's sentences did say what they were about, and this one
            # said 「nothing」. 「过渡句没必要框」 — and a guess here would be a
            # frame drawn over a sentence that is not about the page.
            return []

        pieces = [piece for piece in _CLAUSE_SPLIT.split(segment.text) if piece.strip()]
        if len(pieces) < 2:
            bound = self._resolve_target(segment, page, threshold=threshold)
            if not bound:
                return []
            return [(bound, self._named_with(bound, segment, page, threshold=threshold), 0.0)]

        # Every clause's candidate first, then the choosing. Taken in the order
        # they are read, the first clause to match anything takes the frame and
        # everything after it is judged against that — and on a page whose
        # sections open with the same boilerplate the first match is as likely
        # to be the wrong section as the right one. Judged best-first, the block
        # the sentence is actually about is the one that sets the standard.
        candidates: list[tuple[float, str, str, float]] = []
        spent = 0
        for piece in pieces:
            fraction = spent / max(len(segment.text), 1)
            spent += len(piece)
            clause = NarrationSegment(id=segment.id, text=piece, emphasis=segment.emphasis)
            target = self._resolve_target(clause, page, threshold=threshold)
            if not target:
                continue
            element = page.element(target)
            score = _match(element.text, segment.text) if element else 0.0
            candidates.append((score, target, piece, fraction))

        covered: set[str] = set()
        kept: list[tuple[str, list[str], float]] = []
        seen: set[str] = set()
        by_fit = sorted(candidates, reverse=True, key=lambda c: c[0])
        for _score, target, piece, fraction in by_fit:
            if target in seen:
                continue
            element = page.element(target)
            body = element.text if element else ""
            # The best one is why there is a frame at all; the rest have to earn
            # a second one by explaining a part of the sentence it does not.
            if kept and not _joins(body, segment.text, covered):
                continue
            covered |= _explains(body, segment.text)
            seen.add(target)
            clause = NarrationSegment(id=segment.id, text=piece, emphasis=segment.emphasis)
            kept.append(
                (target, self._named_with(target, clause, page, threshold=threshold), fraction)
            )
        found = sorted(kept, key=lambda k: k[2])
        if not found:
            bound = self._resolve_target(segment, page, threshold=threshold)
            if not bound:
                return []
            return [(bound, self._named_with(bound, segment, page, threshold=threshold), 0.0)]
        return found

    def _resolve_target(
        self, segment: NarrationSegment, page: DocumentPage, *, threshold: float = MENTION_THRESHOLD
    ) -> str | None:
        for ref in segment.element_refs:
            element = page.element(ref)
            if element is None or not _worth_pointing_at(element):
                continue
            if not _is_banner(element, page):
                return ref
        # Nothing bound: find the element this sentence is talking about.
        #
        # Nothing bound. The writer is asked to say which element every sentence
        # is about and now checked on it — see 第六步 of the writing prompt and
        # the 「绑定」 lines in the record — but a sentence that is a transition
        # or a statement about the whole page has nothing to bind to, and an
        # older script may have none at all. Then the camera has to guess, and
        # everything below is that guess. It used to fall
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
            if _is_label(element, page):
                continue
            hit, share = _shared_grams(element.text, segment.text)
            if share < threshold and hit < MENTION_PAIRS:
                continue
            # How much of the sentence this element accounts for, not what
            # share of the element the sentence covers. Sharing everything is
            # easy for a four-character chip — 「揭榜要求」 scored a perfect 1.0
            # against a sentence that went on to say the paragraph underneath
            # it, and the box framed the label while the narrator read the
            # text. The paragraph shares a smaller share of itself and far more
            # of the sentence, which is the thing being said.
            # Both directions, so length stops deciding it; importance only
            # breaks a tie between two elements the sentence fits equally well.
            score = _match(element.text, segment.text) + element.importance * 0.1
            if best is None or score > best[0]:
                best = (score, element.id)
        return best[1] if best else None

    @staticmethod
    def _that_fit(best: str, others: list[str], page: DocumentPage) -> list[str]:
        """As many of the rest as one frame can hold without becoming the page."""
        element = page.element(best)
        if element is None:
            return []
        box = focus_box(element, page)
        kept: list[str] = []
        for other_id in others:
            other = page.element(other_id)
            if other is None or other_id == best or other_id in kept:
                continue
            grown = _union(box, focus_box(other, page))
            if _coverage_box(grown, page) > MAX_UNION_COVERAGE:
                continue
            box = grown
            kept.append(other_id)
        return kept

    def _named_with(
        self,
        best: str,
        segment: NarrationSegment,
        page: DocumentPage,
        *,
        threshold: float,
    ) -> list[str]:
        """The other elements this same clause names, near enough to share a box.

        A clause usually names one thing. When it names several — a heading and
        the four chips under it, a label and the sentence beside it — framing
        only the best-matching one points at a part of what is being said. They
        go in one box.

        Two guards, both about not letting the box become the page. Only things
        the clause matches nearly as well as its best, and only things near what
        is already in the box: two corners of a slide united make a rectangle
        that contains everything between them, which is 「什么都框」.
        """
        element = page.element(best)
        if element is None or not page.width or not page.height:
            return []
        top = _match(element.text, segment.text)
        if top <= 0:
            return []

        rivals: list[tuple[float, str, BBox]] = []
        for other in page.elements:
            if other.id == best or other.kind is ElementKind.TITLE:
                continue
            if not _worth_pointing_at(other) or _is_banner(other, page):
                continue
            hit, share = _shared_grams(other.text, segment.text)
            if share < threshold and hit < MENTION_PAIRS:
                continue
            score = _match(other.text, segment.text)
            if score >= top * COMPANION_SHARE:
                rivals.append((score, other.id, focus_box(other, page)))
        if not rivals:
            return []

        rivals.sort(reverse=True, key=lambda r: r[0])
        box = focus_box(element, page)
        near_x, near_y = page.width * NEAR_SHARE, page.height * NEAR_SHARE
        # What the clause is already accounted for by. A second thing joins the
        # frame only for the part of the clause the first one leaves unexplained.
        covered = _explains(element.text, segment.text)
        taken: list[str] = []
        for _score, other_id, other_box in rivals:
            other = page.element(other_id)
            if other is None:
                continue
            if not _joins(other.text, segment.text, covered):
                continue
            fresh = _explains(other.text, segment.text) - covered
            dx, dy = _gap_between(box, other_box)
            if dx > near_x or dy > near_y:
                continue
            grown = _union(box, other_box)
            if _coverage_box(grown, page) > MAX_UNION_COVERAGE:
                continue
            box = grown
            covered |= fresh
            taken.append(other_id)
        return taken

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

        # When the next marker lands, keyed by the choice it follows. Computed
        # up front because a choice does not know what comes after it.
        starts: list[float] = []
        for choice in choices:
            segment = segments.get(choice.segment_id)
            if segment is None or choice.type is ActionType.RESET:
                starts.append(float("inf"))
                continue
            span = max(segment.end - segment.start, 0.0)
            starts.append(max(0.0, segment.start + span * choice.at_fraction + AUDIO_LEAD))
        next_at = {
            choice.segment_id + str(index): starts[index + 1]
            for index, choice in enumerate(choices)
            if index + 1 < len(starts)
        }

        # A run of choices on the same target is one look at it, not several.
        # 「很长一段都是同一块内容」 came out as a box that appeared, went away
        # four seconds later, and came back for the next sentence — the picture
        # blinking while the narration never left the block. Merged, the box
        # goes up when the block is first mentioned and stays until the
        # narration moves on.
        choices = _merge_runs(choices, segments, page)

        last_target: str | None = None
        for index, choice in enumerate(choices):
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
                at = max(0.0, segment.start + span * choice.at_fraction + AUDIO_LEAD)
                if choice.type is ActionType.POINTER:
                    duration = min(POINTER_DURATION, max(MIN_KEEP_DURATION, span - 0.2))
                elif choice.holds_until:
                    # A merged run: hold the box for as long as the narration
                    # stays on this block, rather than for a fixed four seconds
                    # and then off again while it is still being talked about.
                    duration = max(MIN_ACTION_DURATION, choice.holds_until - at - 0.2)
                else:
                    duration = max(MIN_ACTION_DURATION, min(MAX_ACTION_DURATION, span - 0.2))

            # An action must fit inside its own scene, be long enough to read,
            # and be gone before the next one arrives. Two markers on screen at
            # once is what the contents page looked like: the caption had moved
            # on to 「最后是联合揭榜商业价值」 while the box still sat on 「(四)项目
            # 总体建设内容」 and a red dot had already appeared on (五).
            duration = min(duration, scene.duration - at)
            if (following := next_at.get(choice.segment_id + str(index), None)) is not None:
                duration = min(duration, max(following - at - MARKER_GAP, 0.0))
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
                    # Everything this clause named, so the renderer can draw
                    # one frame around the lot rather than around the best of
                    # them.
                    params={"segment_id": choice.segment_id, "with": list(choice.also)},
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

#: How much wider than a box something has to be before it counts as spanning
#: it rather than sitting beside it, as a share of the box's own width.
SIBLING_WIDTH_TOLERANCE = 0.02


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
    row_gap = page.width * ROW_GAP
    others = [
        other
        for other in page.elements
        if other.id != element.id
        and other.bbox.w > 0
        and not _is_banner(other, page)
        and not _is_backdrop(other, page)
        and not _is_container(other, page)
    ]

    # One at a time, smallest first, so the box grows as far as it can rather
    # than as far as its largest neighbour would take it. Taking every match at
    # once was all-or-nothing: one oversized candidate in the round and the
    # element kept its own box, caption and figure included.
    for _ in range(len(others)):
        fits = [
            grown
            for other in others
            if (
                _belongs(element, box, other, gap=gap, slack=slack)
                or _labels(box, other, gap=gap, slack=slack)
                or _same_row(element, box, other, gap_x=row_gap)
            )
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


def _belongs(element, box: BBox, other, *, gap: float, slack: float) -> bool:
    """Whether `other` is something `box` is part of.

    Something that spans it and is not a bigger piece of writing than it is.
    This is the figure-over-caption shape and a figure carries no text at all;
    the second line of a caption carries about as much as the first. A
    paragraph laid out wide enough to span the heading above it has every
    appearance of containing it and is simply the next block — 「技术牵头方：
    浙大CCAI…」 absorbed the 342-character paragraph under it that way, and a
    frame around the one line the narrator read became a frame around a
    paragraph nobody mentioned.

    It has to span the box horizontally — that is what a figure does to its
    caption and a card to its contents — and either touch it or sit within a
    caption's distance above or below.

    Spanning means wider, not as wide. A column of paragraphs set to one
    measure all span each other exactly, so each is its neighbour's container
    and the box climbs the whole stack: a frame meant for 「行业洗牌加速」 ended
    up starting three items above it. Siblings are the same width; a thing you
    are part of is wider than you.
    """
    # Wider by a little, measured against the box rather than by the alignment
    # slack: the second line of a caption is twenty pixels wider than the
    # first, and a slide's slack is thirty-eight.
    theirs = len((getattr(other, "text", "") or "").strip())
    mine = len((getattr(element, "text", "") or "").strip())
    if theirs > max(LABEL_CHARS, mine):
        return False
    near = other.bbox
    wider = near.w > box.w + max(1.0, box.w * SIBLING_WIDTH_TOLERANCE)
    spans = near.x <= box.x + slack and near.x + near.w >= box.x + box.w - slack and wider
    above = 0 <= box.y - (near.y + near.h) <= gap
    below = 0 <= near.y - (box.y + box.h) <= gap
    overlaps = near.y < box.y + box.h and near.y + near.h > box.y
    return spans and (above or below or overlaps)


#: How many things something has to hold before it is a container rather than
#: a companion.
HOLDS_ENOUGH_TO_BE_A_CONTAINER = 3


def _is_container(element, page: DocumentPage) -> bool:
    """Whether this is the card a thing sits in rather than a thing beside it.

    Grouping exists for companions: a figure and its caption, a card and the
    two lines under it. A column card holding eight paragraphs is neither —
    absorbed, it turns 「这一句」 into 「这一整栏」, and on a three-column page
    that made four consecutive highlights draw the identical rectangle. Which
    is what 「框选会在一段内容中重复框选」 looks like from the outside: the frame
    never moves.

    Holding three or more of the page's other blocks is the test. A caption
    holds nothing; a figure holds nothing; the card holds the column.
    """
    box = element.bbox
    if box.w <= 0 or box.h <= 0:
        return False
    inside = 0
    for other in page.elements:
        if other.id == element.id or not (other.text or "").strip():
            continue
        at = other.bbox
        if (
            at.x >= box.x
            and at.y >= box.y
            and at.x + at.w <= box.x + box.w
            and at.y + at.h <= box.y + box.h
        ):
            inside += 1
            if inside >= HOLDS_ENOUGH_TO_BE_A_CONTAINER:
                return True
    return False


#: How long a lead-in can be and still be a lead-in rather than a paragraph.
LABEL_CHARS = 20

#: How well a second element has to match the same clause, against the best
#: one, before it shares the frame.
COMPANION_SHARE = 0.55

#: And how much of *itself* it has to be there for: the share of its own words
#: that the clause is saying and the first one had not already accounted for.
#:
#: Matching well is not enough, because on a page whose three sections open
#: with the same boilerplate — 「国家人工智能应用中试基地（制造领域石化化工方向）」
#: appears in all three — every section matches every sentence about any of
#: them. One sentence about the first section framed the second and third as
#: well, on the strength of words the first had already accounted for.
#:
#: Measured against the clause instead of against the thing itself, which is
#: what this was at first, the rule punishes exactly the case it exists for. A
#: sentence that walks a row — 「落到供应链、外贸、招投标、产业链图谱、产业头条和
#: 产业内参六项情报」 — is fifty-six characters long, so a twelve-percent floor
#: asks for seven pairs, and a seven-character chip has six. Every chip failed,
#: and the frame stayed on the five-character heading covering nothing.
MIN_NEW_OWN_SHARE = 0.25

#: And never fewer than this many pairs, whatever the proportions say. Two
#: characters in common is a coincidence.
MIN_NEW_PAIRS = 2


def _joins(other_text: str, sentence: str, covered: set[str]) -> bool:
    """Whether this is in the frame for something the frame does not have yet."""
    mine = _explains(other_text, sentence)
    fresh = mine - covered
    body = (other_text or "").strip()
    own = max(len(body) - 1, 1)
    return len(fresh) >= MIN_NEW_PAIRS and len(fresh) / own >= MIN_NEW_OWN_SHARE


def _explains(text: str, sentence: str) -> set[str]:
    """Which pairs of the sentence this element's own words account for."""
    body, said = (text or "").strip(), (sentence or "").strip()
    if len(body) < 3 or len(said) < 3:
        return set()
    have = {body[i : i + 2] for i in range(len(body) - 1)}
    return {said[i : i + 2] for i in range(len(said) - 1)} & have


def _pairs(sentence: str) -> int:
    said = (sentence or "").strip()
    return max(len(said) - 1, 1)

#: How far apart two things may be and still be one thing being talked about,
#: as a share of the page. Two corners united make a rectangle holding
#: everything between them.
NEAR_SHARE = 0.22

#: A frame may not grow past this much of the page. Past it, it is not pointing
#: at anything.
MAX_UNION_COVERAGE = 0.30


def _gap_between(one: BBox, two: BBox) -> tuple[float, float]:
    """How far apart two boxes are, horizontally and vertically. Zero if they touch."""
    dx = max(0.0, max(one.x, two.x) - min(one.x + one.w, two.x + two.w))
    dy = max(0.0, max(one.y, two.y) - min(one.y + one.h, two.y + two.h))
    return dx, dy


#: How far apart a lead-in and its body may sit and still be one line, as a
#: share of the page's width.
#:
#: It is the number that tells a row from a column. On a page laid out as
#: 「产业链结构 ｜ 原料、产品、中间环节… 」 the two sit 4% of the page apart; on a
#: page laid out as three cards side by side, the nearest thing to the right of
#: 「市场：AI重构行业淘汰节奏」 is the next card's heading, 10% away. Six percent
#: takes the first and leaves the second — which matters, because swallowing
#: the card beside it turns 「这一个」 into 「这两个」.
ROW_GAP = 0.06


def _same_row(element, box: BBox, near, *, gap_x: float) -> bool:
    """A lead-in and the body beside it, on one line.

    `_labels` knows the shape where the lead-in sits *above* what it leads
    into. Plenty of decks lay the same thing out sideways — a short name in a
    column of its own and the sentence it names to the right of it — and there
    the two are never grouped, so a script that says 「产业链结构，原料、产品、
    中间环节…」 got a frame around four characters. 「只框个名称的」 is that.

    One of the two has to be short enough to be a name and the other longer
    than it: two things of similar length side by side are two things, not one
    thing and its label.
    """
    beside = near.bbox
    overlap = min(box.y + box.h, beside.y + beside.h) - max(box.y, beside.y)
    if overlap < 0.6 * min(box.h, beside.h):
        return False
    space = (
        beside.x - (box.x + box.w) if beside.x >= box.x else box.x - (beside.x + beside.w)
    )
    if not 0 <= space <= gap_x:
        return False
    mine = len((getattr(element, "text", "") or "").strip())
    theirs = len((near.text or "").strip())
    if not mine or not theirs:
        return False
    return (mine <= LABEL_CHARS < theirs) or (theirs <= LABEL_CHARS < mine)


def _labels(box: BBox, near, *, gap: float, slack: float) -> bool:
    """Whether `near` is the lead-in that `box` is the body of.

    `_belongs` asks whether the neighbour *spans* the box, which is the shape a
    figure has over its caption and a card over its contents. A lead-in has the
    opposite shape: 「同行差距放大：」 is seven characters over a two-line
    paragraph, so it is narrower than the thing it introduces and no amount of
    spanning will ever find it. The frame came out around the paragraph with
    the words naming it sitting just outside the line — which reads as a box
    that missed.

    So the other shape is named too: flush to the same left edge, directly
    above within a caption's distance, no wider, and short enough to be a
    label. Its own line, not the paragraph before it.
    """
    text = (getattr(near, "text", "") or "").strip()
    if not text or len(text) > LABEL_CHARS:
        return False
    at = near.bbox
    aligned = abs(at.x - box.x) <= slack
    above = 0 <= box.y - (at.y + at.h) <= gap
    return aligned and above and at.w <= box.w and at.h <= box.h


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


def _is_label(element, page: DocumentPage) -> bool:
    """Whether this element is the caption on the block below it."""
    text = (element.text or "").strip()
    if not text or len(text) > LABEL_CHARS or element.bbox is None:
        return False
    height = page.height or 1080
    bottom = element.bbox.y + element.bbox.h
    for other in page.elements:
        if other.id == element.id or other.bbox is None or not (other.text or "").strip():
            continue
        below = other.bbox.y >= bottom - element.bbox.h * 0.5
        near = other.bbox.y - bottom <= height * LABEL_GAP
        longer = len(other.text.strip()) >= len(text) * LABEL_BODY_RATIO
        if below and near and longer:
            return True
    return False


#: How many things have to sit beside a short line before it is heading them
#: rather than standing among them.
HEADS_A_ROW = 2


def _just_a_name(element, page: DocumentPage) -> bool:
    """Whether framing this alone would be framing a heading.

    Short is not enough. A contents page is a column of short lines with
    nothing underneath any of them, and 「(一)背景及技术牵头方」 *is* the content
    there — a rule that went by length alone stopped framing the one thing
    those pages have.

    Three shapes, and they are all 「this names something else」:

    - a longer block underneath it (`_is_label`) — 「招募目的」;
    - a longer block beside it on the same line — 「产业链结构 ｜ 原料、产品…」;
    - a row of things beside it on the same line — 「场景应用层」, whose
      neighbours are chips as short as it is, which is why looking for a
      *longer* one missed it.

    A number or a picture is exempt: 「130305家」 is short and is the whole
    point of pointing at it.
    """
    if element is None or element.kind in (
        ElementKind.NUMBER,
        ElementKind.CHART,
        ElementKind.IMAGE,
    ):
        return False
    text = (element.text or "").strip()
    if not text or len(text) > LABEL_CHARS or element.bbox is None:
        return False
    if _is_label(element, page):
        return True
    # A short subtitle is a heading by its own account — that is what the kind
    # means. 「场景应用层」 is one, and the things beside it are chips as short
    # as it is, so neither of the shapes below finds it.
    if element.kind is ElementKind.SUBTITLE:
        return True

    mine = element.bbox
    gap_x = (page.width or 1920) * ROW_GAP
    beside = 0
    for other in page.elements:
        body = (other.text or "").strip()
        if other.id == element.id or other.bbox is None or not body:
            continue
        at = other.bbox
        overlap = min(mine.y + mine.h, at.y + at.h) - max(mine.y, at.y)
        if overlap < 0.6 * min(mine.h, at.h) or at.x < mine.x + mine.w:
            continue
        if len(body) >= len(text) * LABEL_BODY_RATIO and at.x - (mine.x + mine.w) <= gap_x:
            return True
        beside += 1
    return beside >= HEADS_A_ROW


def frame_of(target: str, also: list[str], page: DocumentPage) -> BBox | None:
    """The one box drawn for a choice: everything it named, united."""
    element = page.element(target)
    if element is None:
        return None
    box = focus_box(element, page)
    for other_id in also or []:
        other = page.element(other_id)
        if other is not None:
            box = _union(box, focus_box(other, page))
    return box


def _frame_key(target: str | None, page: DocumentPage, also: list[str] | None = None) -> str | None:
    """What will actually be drawn for this target, as something comparable.

    Not the element id. A lead-in and the paragraph under it are two elements
    and one block, so two segments pointing at them separately produce two
    actions that draw the identical rectangle — the box goes up, goes down, and
    comes back in exactly the same place. From a seat in front of the film that
    is the same complaint as framing something twice, because it *is* framing
    it twice.
    """
    if not target:
        return None
    box = frame_of(target, also or [], page)
    if box is None:
        return target
    return f"{round(box.x)},{round(box.y)},{round(box.w)},{round(box.h)}"


def _merge_runs(
    choices: list[ActionChoice], segments: dict, page: DocumentPage
) -> list[ActionChoice]:
    """One look per thing: consecutive ones become a hold, later ones are dropped.

    Two rules, and they are the same rule — 「要么就一直框着，要么就框一开始一
    下，不要讲一会框一下」.

    Consecutive looks at the same block are one box that stays up. Looks that
    come back to it later are not: on one slide the same block was framed at
    15s, again at 43s and again at 62s, which reads as the camera losing its
    place and going back. The first time it was framed is when it was
    introduced; after that the page has moved on, and drawing nothing is the
    honest answer.

    「Same」 is by the box that gets drawn, not by the element that was matched
    — see `_frame_key`.
    """
    keys = [_frame_key(choice.target, page, choice.also) for choice in choices]
    merged: list[ActionChoice] = []
    merged_keys: list[str | None] = []
    for index, choice in enumerate(choices):
        key = keys[index]
        segment = segments.get(choice.segment_id)
        if segment is None:
            merged.append(choice)
            merged_keys.append(key)
            continue
        end = segment.end
        if merged and merged_keys[-1] and merged_keys[-1] == key:
            merged[-1] = merged[-1].model_copy(update={"holds_until": end})
            continue
        merged.append(choice.model_copy(update={"holds_until": end}))
        merged_keys.append(key)

    # Now the repeats. Which one to keep is not 「the first」: a page whose
    # opening sentence says 「用进出口数据找海外机会」 brushes past the card headed
    # 「海外机会清单」 nine seconds before the script actually describes it, and
    # keeping the first framed the right card at the wrong moment. The one that
    # matches best is the one the narration is about.
    best: dict[str, int] = {}
    for index, choice in enumerate(merged):
        key = merged_keys[index]
        if not key:
            continue
        keep = best.get(key)
        if keep is None or choice.match > merged[keep].match:
            best[key] = index
    return [
        choice
        for index, choice in enumerate(merged)
        if not merged_keys[index] or best.get(merged_keys[index]) == index
    ]


def _worth_looking_at(page: DocumentPage) -> bool:
    """Whether this page has anything a camera could usefully point at."""
    return sum(1 for element in page.elements if _worth_pointing_at(element)) >= 2


def _shared_grams(element_text: str, sentence: str) -> tuple[int, float]:
    """`(how many, what share)` of `element_text` turns up in `sentence`."""
    element_text, sentence = element_text.strip(), sentence.strip()
    if len(element_text) < 3 or len(sentence) < 3:
        return (0, 0.0)
    grams = {element_text[i : i + 2] for i in range(len(element_text) - 1)}
    said = {sentence[i : i + 2] for i in range(len(sentence) - 1)}
    hit = len(grams & said)
    return (hit, hit / len(grams))


def _match(element_text: str, sentence: str) -> float:
    """How well these two are about the same thing, in both directions.

    Counting shared pairs alone hands the page to whichever element has the
    most text in it. Measured on one slide: a block reading 「构建覆盖石化行业
    创新链、产业链、人才链、资金链……」 was framed three separate times on the
    same page, because every sentence that said 创新链 or 产业链 shared enough
    pairs with it to beat the card actually being described.

    So the two are compared symmetrically — shared pairs against the size of
    both — which is high only when the element and the sentence are close to
    the same thing, and falls off whether the element is far longer than the
    sentence or far shorter.
    """
    element_text, sentence = element_text.strip(), sentence.strip()
    if len(element_text) < 3 or len(sentence) < 3:
        return 0.0
    grams = {element_text[i : i + 2] for i in range(len(element_text) - 1)}
    said = {sentence[i : i + 2] for i in range(len(sentence) - 1)}
    return 2 * len(grams & said) / (len(grams) + len(said))


def _mentioned(element_text: str, sentence: str) -> float:
    """What share of `element_text` turns up in `sentence`, by character bigram."""
    return _shared_grams(element_text, sentence)[1]


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
