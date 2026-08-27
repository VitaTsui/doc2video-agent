"""presentation-narration — turn a script into timed scenes.

This service does not write the script; whoever calls it does. What lives here
is the part that has to be arithmetic rather than judgement:

* **The duration budget.** Each page gets a share of the target length, weighted
  by page type and emphasis, and that share becomes a character budget the
  caller writes to. Deciding length *after* TTS would mean re-voicing everything,
  so the budget has to exist before a word is written — which is why it is
  published to the caller (``narration_guide``) rather than kept internal.
* **Applying what comes back.** Splitting a page's script into sentence-level
  segments, binding those to page elements, and estimating each scene's duration.

``apply`` takes the caller's text; ``run`` falls back to a placeholder script so
the pipeline stays runnable end to end without one (useful for testing the
render path, not for a video anyone would watch).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context

from pydantic import AliasChoices, BaseModel, Field, model_validator

from ..core import ledger, telemetry, tuning
from ..core.ids import scene_id
from ..schemas import DocumentPage, NarrationSegment, PageType, Scene, SceneVisual, VisualType
from ..tools.llm import model_schema
from ..tools.tts import estimate_duration
from .base import ProgressFn, Skill, load_prompt
from .review import blocks_of, density, page_share_chars, worth_naming

# Pages per model call. Small enough that a long deck cannot overrun the
# output budget, large enough that each page can see its neighbours.
BATCH_SIZE = 4
# How far past the requested length a script may run before it is cut back.
# Some slack on purpose: trimming a page costs it a sentence, and a video ten
# percent long is not worth a sentence.
DURATION_TOLERANCE = 0.10

#: Roughly what naming one thing costs, when working out how many things a
#: shortened page can still afford to name.
MIN_ITEM_CHARS_FOR_FLOOR = 18

#: How many times one page is asked to say itself shorter. One pass answers a
#: 92-character budget with 159 — a real cut that is still nearly twice the
#: number.
COMPRESSION_ROUNDS = 3

#: Pages there is nothing to save on. A cover is a title and the names of the
#: people who wrote it; a contact page is an address. Neither is where a film
#: gets long — the cover ran eight characters over — and both are made almost
#: entirely of proper nouns, which is the one kind of text that cannot be said
#: shorter. Asked to fit anyway, the only thing left to give up is a piece of a
#: name: 「宁波城知产业链数据科技有限公司」 came back as 「…数据科技」.
#: 「封面不要压字数。」
NEVER_COMPRESSED = (PageType.COVER, PageType.CONTACT)

#: How far past its ceiling a page has to be to be worth another round. A page
#: at 1.2× is close enough — the round it would cost is a minute of waiting for
#: a handful of characters.
KEEP_COMPRESSING = 1.5

#: How much of a page a rewrite has to take off to be worth having asked for
#: it. A round that shaves a few characters is not compressing the page, it is
#: filing words off sentences that already say what they say. Page 5 of a real
#: deck bought eight characters that way — 199 → 191 against a ceiling of 136,
#: nowhere near it either way — and paid 「中心主任是庄越挺教授」 and two
#: sentences' grammar for them. Keep what the round before it said instead.
WORTH_ANOTHER_ROUND = 0.08

#: About as much as saying the same things in fewer words can buy. Past this,
#: a page only gets shorter by not saying one of them — and a page told it may
#: not drop anything answers a demand it cannot meet by breaking sentences
#: instead: 「再看AI技术优势，也就是技术研发成果的建设载体」 came back as the
#: bare phrase, subject gone. So the depth of the cut decides too, not the
#: page's density alone.
REPHRASING_BUYS = 0.20

# Per-page-type weights for splitting the total duration budget.
TYPE_WEIGHT = {
    PageType.COVER: 0.35,
    PageType.AGENDA: 0.45,
    PageType.SECTION: 0.4,
    PageType.CONTENT: 1.0,
    PageType.CHART: 1.25,
    PageType.ARCHITECTURE: 1.35,
    PageType.COMPARISON: 1.15,
    PageType.CASE: 1.15,
    PageType.SUMMARY: 0.8,
    PageType.CONTACT: 0.25,
    PageType.OTHER: 0.9,
}
EMPHASIS_MULTIPLIER = 1.9
# A page with no text of its own still takes a moment; a page with a wall of it
# does not get to eat the whole film.
BASE_VOLUME = 60
MAX_VOLUME = 900
# The page type still counts, but it no longer decides: reading a dense chart
# page takes as long as its labels take to read, whatever a table says a chart
# page is usually worth. As an exponent, 0.5 keeps the ordering and halves the
# spread (1.35 → 1.16, 0.35 → 0.59).
TYPE_INFLUENCE = 0.5
MIN_SCENE_SECONDS = 4.0
# The most a page's own text can claim as a floor. Past this the page is dense
# enough that some of it will have to go unread whatever the deck's length.
MAX_FLOOR_SECONDS = 20.0
# How much of the film the floors may claim between them before they are all
# scaled down. The rest is divided by weight, which is what gives a dense page
# more than its own floor.
FLOOR_SHARE = 0.6
MAX_SCENE_SECONDS = 75.0

SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")

TRANSITIONS = [
    "我们先从{title}说起。",
    "接着来看{title}。",
    "在此基础上，{title}。",
    "说到这里，就要提到{title}。",
    "顺着这个思路，{title}。",
]


# What a style name means to someone writing the words. The enum value alone
# ("lively") tells the model a category; these tell it what to do differently,
# which is the only form a style instruction can be obeyed in.
STYLE_BRIEF = {
    "professional": "专业、克制。用行业里通行的说法，不解释常识，不铺陈形容词。",
    "tech": "技术向。讲清楚机制与取舍，敢用具体数字和术语，句子干脆。",
    "lively": "活泼。多用短句和口语连接，可以带一点反问和轻微夸张，不端着。",
    "casual": "轻松。像同事之间讲一件事，允许口语词和插入语，不用书面套话。",
    "formal": "正式。完整句、书面用词，不用口语缩略，语气持重。",
}


class SegmentDraft(BaseModel):
    text: str
    element_refs: list[str] = Field(default_factory=list)
    emphasis: bool = False


class PageNarration(BaseModel):
    """One page's script, as the model returns it.

    Two accommodations, both because the same thing has two reasonable names
    and refusing one of them costs a whole batch its script:

    * the page is `index` here and `page_number` in most people's prompts;
    * the page's text is the segments, so a model that sends only those is not
      missing anything. Asking for `narration` as well was asking for the same
      words twice, and the second copy is the one that could disagree.
    """

    index: int = Field(validation_alias=AliasChoices("index", "page_number", "page"))
    segments: list[SegmentDraft] = Field(default_factory=list)
    narration: str = ""

    @model_validator(mode="after")
    def _fill_narration(self) -> PageNarration:
        if not self.narration.strip() and self.segments:
            self.narration = "".join(segment.text for segment in self.segments)
        return self


class NarrationResult(BaseModel):
    pages: list[PageNarration]


def _density_note(page, budget: int) -> str:
    """How much of this page to tell — as a count, not as a ratio.

    The writer and the reviewer were reading from different sheets. `worth_naming`
    already says how many of a page's blocks a script should get through: a page
    with three is read, a page with sixteen is chosen from — eight of them. But
    what the writer was handed was a band, and the middle band said 「每一处都要
    点到」. So on the sixteen-block page it named twelve, against a target of
    eight, and the film spent seventy seconds reading out sub-items nobody asked
    to hear. 「有些点或细项是能不用讲的」 is that instruction, in one sentence.

    So the writer is handed the same number the reviewer is holding. Where the
    number covers the page there is nothing to choose and it says so; where it
    does not, choosing is the whole instruction, and what to spend the places on
    — the skeleton first, then the biggest of what is left — is said plainly.
    """
    # Cover, contents and section pages are not told by volume — they have
    # their own rule, and it is the opposite of this one. A cover at 1.7× was
    # handed 「每一处都要点到」 and read its four lines out one at a time,
    # date included, which is precisely what its own instruction forbids.
    if page.page_type in (PageType.COVER, PageType.AGENDA, PageType.SECTION):
        return ""
    count = len(blocks_of(page))
    if count == 0:
        return ""
    keep = worth_naming(page)
    ratio = density(page, budget)

    # Two different questions, and a page can answer them differently. How many
    # things to name is the count; how much of each to say is the ratio.
    #
    # A page whose text fits its budget has nothing to choose, however many
    # boxes it is in: eight one-line bullets that all fit are eight bullets to
    # say, and telling it to drop two is telling it to leave things out for no
    # reason. And 「技术牵头方」 — three labels and one 342-character paragraph —
    # is four blocks, all four worth naming, carrying two and a half times what
    # it can say: everything gets named, nothing gets read out whole.
    if ratio <= 1.2:
        return f"这一页有 {count} 处内容，都讲得下：按页面原文讲，一处一句，不要概括。"
    if keep >= count:
        return (
            f"这一页 {count} 处都要讲到，但文字是预算的 {ratio:.1f} 倍："
            "长的那几处只取名称、数字和结论，不要照读整段。"
        )
    return (
        f"这一页有 {count} 处内容，讲其中 {keep} 处——文字是预算的 {ratio:.1f} 倍，"
        "讲不完，也不该讲完。\n"
        f"挑哪 {keep} 处由你定：先看每一栏、每个小标题，再看哪几处最要紧。"
        "但**挑中的每一处都要讲成话**——它是什么、做什么用、页面给了什么数字。"
        "宁可少挑两处，也不要把每一处都压成一个名字：一串名词念完，听的人什么都没得到。\n"
        "没挑中的，一个字都不要提，也不要用「等等」「以及其他」把它们带过去。"
    )


#: How many batches to write at once. Small on purpose: measured at 59s
#: against 37s for two, which is worth having, but the ceiling here is not the
#: machine — it is whatever the model sits behind, and one transient failure
#: while probing this was reminder enough.
MAX_WRITERS = 3


def writing_workers(count: int, configured: int) -> int:
    """How many batches to write at once."""
    if count <= 1:
        return 1
    if configured > 0:
        return max(1, min(configured, count))
    return max(1, min(count, MAX_WRITERS))


class NarrationSkill(Skill):
    name = "presentation-narration"
    description = "生成适合目标受众和时长的讲稿"

    def run(
        self, written: dict[int, str] | None = None, *, progress: ProgressFn | None = None
    ) -> None:
        """Write the script ourselves — the path taken when nobody supplied one.

        With a model configured this is a real script. Without one it is
        placeholder text: renderable, correctly timed, and meaningless — which
        is why it is recorded as a degradation rather than passed off as a
        result. The caller-written path (``apply``) remains the primary one.

        ``written`` is the half-written case, which is the common one: someone
        has typed the pages they care about and wants the rest filled in. Those
        pages are not rewritten, not trimmed, and not shown to the model as
        something to improve — they are shown as context, because a page whose
        neighbours are already written has to continue from them rather than
        open on its own.
        """
        pages = self._pages()
        if not pages:
            return
        budgets = self._allocate_budget(pages)
        kept = self._kept(pages, written)
        if len(kept) == len(pages):
            # Nothing to write. Still worth running: the text has to be split
            # into segments and timed before anything downstream can use it.
            drafts = {index: _as_draft(index, text) for index, text in kept.items()}
        else:
            drafts = self.try_llm(
                lambda: self._write_with_model(pages, budgets, kept, progress=progress),
                lambda: self._placeholder(pages, budgets, kept),
                what="讲稿",
            )
        drafts = self._repair_drafts(pages, budgets, drafts, frozen=set(kept))
        drafts = self._fit_duration(pages, budgets, drafts, frozen=set(kept))
        self._build_scenes(pages, drafts)
        self.log.info(
            "讲稿完成：%d 个场景（其中 %d 页是人写的）", len(self.project.scenes), len(kept)
        )

    @staticmethod
    def _kept(pages: list[DocumentPage], written: dict[int, str] | None) -> dict[int, str]:
        """The pages someone has actually written, in page order."""
        indexes = {page.index for page in pages}
        return {
            index: text.strip()
            for index, text in sorted((written or {}).items())
            if index in indexes and text and text.strip()
        }

    # -- repair ---------------------------------------------------------
    def _repair_drafts(
        self,
        pages: list[DocumentPage],
        budgets: dict[int, float],
        drafts: dict[int, PageNarration],
        frozen: set[int] | None = None,
    ) -> dict[int, PageNarration]:
        """Rewrite the pages that did not finish what they started.

        Two faults, one repair — both are 「讲了一半就翻页」 and both are decided
        by the same deterministic rules the quality report uses, so a page is
        judged the same way before it is spoken as after.

        「平台上有三块开放机制。」 and the page ends — the three are on the slide
        in front of the viewer, and the sentence set up an expectation the film
        never pays. Saying so in the writing prompt helped and did not settle
        it: two pages in thirty before, one after. So the draft is checked
        against the same deterministic rule the quality report uses, and the
        pages that fail are written again, once, with the specific sentence
        quoted back.

        Only those pages, and only once: a page that comes back dangling twice
        is a page the model cannot do better on, and re-asking forever costs
        minutes for nothing.
        """
        from .review import _dangling_counts, missed_items

        frozen = frozen or set()
        by_index = {page.index: page for page in pages}
        broken: dict[int, str] = {}
        for index, draft in drafts.items():
            if index in frozen or (page := by_index.get(index)) is None:
                continue
            if found := _dangling_counts(draft.narration):
                phrase, said, named = found[0]
                broken[index] = (
                    f"上一稿在这一页写了「{phrase}」，却只点到 {named} 项。"
                    f"请把这 {said} 项的名字按页面上的写法都说出来；"
                    "如果字数装不下，就不要报数，直接讲其中最重要的一两项。"
                )
                continue
            walked, affordable, missed = missed_items(draft.narration, page)
            if affordable and walked < affordable and missed:
                # Told as a shape, not as a complaint. 「补进去」 left the model
                # free to keep the paragraph it had written about one card and
                # append nothing — measured on eleven reworked pages, the count
                # did not move. A sentence each, in page order, is something it
                # can actually execute, and it is what 「先横向铺开」 means.
                # Coverage *within* the budget. Asked without the number, the
                # rework covered the page by reading every card's body as well
                # — the deck went from 2103 characters to 5973, a 24-minute
                # film against an 8-minute request. Breadth is the thing worth
                # having; the words per item are what has to give.
                want = min(affordable, len(missed) + walked)
                budget = self._char_budget(budgets[index])
                each = max(12, budget // max(want, 1))
                broken[index] = (
                    f"上一稿只讲到这一页的 {walked} 处内容，这一页值得讲到 {affordable} 处——"
                    "一张卡片讲得再透，旁边三张没讲到，观众看着屏幕上的它们听你翻页。\n"
                    f"重写这一页：写成 {want} 句左右，**按页面顺序一处一句**，"
                    f"每句 {each} 字上下（这一页平均每处该讲多少），"
                    "带上那一处自己的名字和它最要紧的一个信息。"
                    f"整页不要超过 {budget} 字。\n"
                    f"漏掉的是：「{'」「'.join(missed[:6])}」。\n"
                    "每句短一点没关系，讲不全才是问题；细节留给观众自己看屏幕。"
                )
        if not broken or not self.llm.available:
            return drafts

        self.log.info("需要返工的页面：%s", sorted(broken))
        for index, note in broken.items():
            page = by_index.get(index)
            if page is None:
                continue
            before = drafts[index].narration if index in drafts else ""
            try:
                # Openable, like every other call that produced something. Why
                # the page was sent back, and the two versions to compare: a
                # record that says 「第 13 页｜返工」 and cannot be opened tells
                # you a page was rewritten and nothing about what changed.
                with ledger.call(self.llm.source, f"第 {index} 页｜返工") as made:
                    result = self.llm.complete_json(
                        self._prompt([page], budgets, position=index - 1) + f"\n\n# 返工\n{note}",
                        schema=model_schema(NarrationResult),
                        system=load_prompt("narration"),
                        max_tokens=self.ctx.settings.llm_max_tokens,
                    )
                    rewritten = NarrationResult.model_validate(result).pages
                    fixed = next(
                        (c.narration.strip() for c in rewritten if c.index == index), ""
                    )
                    made.append(ledger.text_artifact("为什么返工", note, page=index))
                    if before:
                        made.append(ledger.text_artifact("返工前", before, page=index))
                    if fixed:
                        made.append(ledger.text_artifact("返工后", fixed, page=index))
            except Exception as exc:  # noqa: BLE001 - a failed repair keeps the draft
                self.log.warning("第 %d 页返工失败，保留原稿：%s", index, exc)
                continue
            for candidate in rewritten:
                if candidate.index == index and candidate.narration.strip():
                    drafts[index] = candidate
        return drafts

    # -- fitting --------------------------------------------------------
    def _fit_duration(
        self,
        pages: list[DocumentPage],
        budgets: dict[int, float],
        drafts: dict[int, PageNarration],
        frozen: set[int] | None = None,
    ) -> dict[int, PageNarration]:
        """Bring the script back to the length that was asked for.

        The budget exists before a word is written and the model overruns it
        anyway — on a real 30-page deck it wrote 38% past, which came out as a
        video 160 seconds longer than the one that was ordered. Review reported
        that and nothing acted on it, so "做成八分钟" quietly meant eleven.

        Not by speeding the voice up: that is the thing a listener complains
        about, and it makes a long script into a rushed one rather than a
        shorter one. Pages are compressed instead, worst overrun first, and the
        ones the user marked as important are compressed last.

        ``frozen`` pages are never cut. Someone wrote those words on purpose,
        and silently deleting a sentence out of them would be the worst thing
        this function could do — the overrun is the model's to pay for.
        """
        target = float(self.project.intent.duration)
        if target <= 0 or not drafts:
            return drafts
        # Nobody asked for a length: the target came from the deck's own content,
        # so it is a plan rather than a promise. A plan is still worth keeping —
        # left alone, the writer ran 2.4× past it (24.4 minutes against the 13.3
        # its own pages proposed), which is every item said at 46 characters
        # where the estimate says 18–28. But it is kept only by rewriting, never
        # by cutting: nothing here may decide on its own that a page loses its
        # last item to a number nobody asked for.
        rewrite_only = not self.project.intent.duration_stated

        silence = self._page_silence() * len(pages)
        pace = self._pace()

        def spoken(text: str) -> float:
            return len(text) / pace

        total = sum(spoken(d.narration) for d in drafts.values()) + silence
        if total < target * (1 - DURATION_TOLERANCE):
            # Nothing to do about it here — a script can be cut to length and
            # cannot be filled out without writing more of it — but a video a
            # sixth shorter than the one that was asked for should not arrive
            # without anyone having said so. Trimming records its side; this
            # is the other.
            self.log.info(
                "讲稿比目标短 %.0f 秒（%.0fs / 目标 %.0fs）", target - total, total, target
            )
            telemetry.record_degradation(
                "讲稿", f"比目标时长短 {target - total:.0f} 秒，成片会短一截"
            )
            return drafts
        if total <= target * (1 + DURATION_TOLERANCE):
            return drafts

        # Whose fault it is, most-to-least: how far past its own budget a page
        # ran, discounted by how much the user said it matters.
        emphasis = set(self.project.intent.emphasis_pages)
        frozen = frozen or set()
        over = []
        for page in pages:
            draft = drafts.get(page.index)
            if draft is None or page.index in frozen:
                continue
            if page.page_type in NEVER_COMPRESSED:
                continue
            allowed = budgets.get(page.index, 0.0)
            excess = spoken(draft.narration) - allowed
            if excess <= 0:
                continue
            over.append((excess * (0.4 if page.index in emphasis else 1.0), page.index, allowed))
        over.sort(reverse=True)

        self.log.info(
            "讲稿比目标长 %.0f 秒（%.0fs / 目标 %.0fs），压 %d 页",
            total - target,
            total,
            target,
            len(over),
        )
        telemetry.record_degradation(
            "讲稿", f"超出目标时长 {total - target:.0f} 秒，压缩 {len(over)} 页"
        )

        from .review import _dangling_counts, missed_items

        by_index = {page.index: page for page in pages}
        trimmed = dict(drafts)
        for _, index, allowed in over:
            if total <= target * (1 + DURATION_TOLERANCE):
                break
            draft = trimmed[index]
            shorter = _trim_to(draft.narration, int(allowed * pace))
            if len(shorter) >= len(draft.narration):
                continue
            # Trimming takes sentences off the end, and the end of a page is
            # where its last items are. 「平台上有三块开放机制。」 — the page that
            # started this — is what a trim looks like from the outside: the
            # script named three, the trimmer removed the naming, and the film
            # announced a list and moved on. A page is only shortened while it
            # still tells the whole page.
            page = by_index.get(index)
            costs_content = bool(_dangling_counts(shorter)) and not _dangling_counts(
                draft.narration
            )
            if page is not None and not costs_content:
                costs_content = (
                    missed_items(shorter, page)[0] < missed_items(draft.narration, page)[0]
                )
            if costs_content or rewrite_only:
                # Cutting from the end takes the page's last items with it. Ask
                # for the same page said in fewer words instead — one call, and
                # it is the only way to honour both 「十五分钟」 and 「把这一页讲
                #全」, which is what the person asked for on the same day.
                # More than once, because one pass does not reach the number:
                # asked to bring 300 characters down to 92 the model answers
                # with 159, a real cut and still 1.7× the budget.
                #
                # But only while there is a long way to go. Every round is a
                # model call, and three rounds over twenty-five over-budget
                # pages is seventy-five of them — measured at over fifty
                # minutes for one script, which is longer than the render. A
                # page already close to its number is close enough; the last
                # few characters are not worth another minute of waiting.
                ceiling = int(allowed * pace)
                current = draft
                for _round in range(COMPRESSION_ROUNDS):
                    rewritten = self._compress_with_model(
                        page, current, ceiling, attempt=_round + 1
                    )
                    if rewritten is None:
                        break
                    current = rewritten
                    if len(current.narration) <= ceiling * KEEP_COMPRESSING:
                        break
                if current is draft:
                    self.log.info("第 %d 页压不动：剪会少讲内容，改写也没写短", index)
                    continue
                total -= spoken(draft.narration) - spoken(current.narration)
                trimmed[index] = current
                continue
            total -= spoken(draft.narration) - spoken(shorter)
            trimmed[index] = PageNarration(index=index, narration=shorter, segments=[])
        return trimmed

    def _compress_with_model(
        self,
        page: DocumentPage | None,
        draft: PageNarration,
        allowed: int,
        *,
        attempt: int = 1,
    ) -> PageNarration | None:
        """The same page, said shorter. None when it could not be done.

        Trimming removes sentences, and a page's last sentences are its last
        items — 「平台上有三块开放机制。」 is what that looks like from the
        outside. Rewriting keeps every item and takes the words out of each
        one, which is the only way a stated length and a fully-told page can
        both be true.

        Checked rather than trusted: the rewrite has to be shorter *and* still
        name what the original named, or it is thrown away.
        """
        if page is None or not self.llm.available:
            return None

        # How much of the page this rewrite still has to walk. A page whose
        # own volume is far past what it can say is a page to summarise: told
        # 「一处都不能少」 it has nothing left to give up and comes back the same
        # length — six pages 「压不动」 on a real deck, the worst of them 2.6×
        # over. A page that nearly fits keeps everything, because there the
        # words are the fat.
        from .review import (
            DENSE_ENOUGH_TO_SUMMARISE,
            density,
            page_share_chars,
            worth_naming,
        )

        must_cut = 1 - allowed / max(len(draft.narration), 1)
        may_summarise = (
            density(page, page_share_chars(page)) > DENSE_ENOUGH_TO_SUMMARISE
            or must_cut > REPHRASING_BUYS
        )
        if may_summarise:
            keep = worth_naming(page)
            give_up = (
                f"**要减的是处数，不是字。** 这一页内容装不下，讲到 {keep} 处就够——"
                "留骨架（小标题、每一栏的名称、数字和结论），"
                "举例、括号里的补充、同一条里的展开，整条不讲。\n"
                "但**报了数就要点名**：说了「五部分」就得点出五个，装不下就别报数。\n"
            )
        else:
            give_up = (
                "**内容一处都不能少**：现在讲到的每一处，改写后还要讲到。\n"
                "这一页能省的是说法——同一件事换个短一点的说法，"
                "重复的表述合并成一句。\n"
            )
        note = (
            f"这一页现在 {len(draft.narration)} 字，要压到 {allowed} 字以内。\n"
            + give_up
            # What this used to say was 「删修饰词、去掉可有可无的连接词……宁可每句
            # 只剩七八个字」, and it got exactly that. A good draft came back as
            # 「简称CCAI，是国家首批之一」 — the head noun deleted as a modifier,
            # 「之一」 left hanging — and 「再看AI技术优势，也就是技术研发成果的建设
            # 载体」 came back as the bare phrase with its subject gone. Filing
            # words off sentences is the one way of getting shorter that damages
            # what is left; dropping a whole point costs nothing but the point.
            + "**留下来的每一句，还是一句完整的话**——主语、谓语、中心词都在，"
            "念出来听得懂。见「短，但仍然是一句完整的话」那一节。\n"
            "压不到也不要压成电报：宁可差几个字，宁可整件事不讲。\n\n"
            f"现在的讲稿：\n{draft.narration}"
        )
        try:
            # The round is part of what to call this: three passes over one page
            # are three records, and without the number the account repeated
            # 「第 17 页，压到 175 字」 three times and read as something stuck.
            said = f"第 {page.index} 页｜压到 {allowed} 字"
            if attempt > 1:
                said += f"｜第 {attempt} 轮"
            with ledger.call(
                self.llm.source, said, covers=[ledger.page_key(page.index)]
            ) as made:
                result = self.llm.complete_json(
                    self._prompt([page], {page.index: allowed / self._pace()},
                                 position=page.index - 1) + f"\n\n# 返工\n{note}",
                    schema=model_schema(NarrationResult),
                    system=load_prompt("narration"),
                    max_tokens=self.ctx.settings.llm_max_tokens,
                )
                pages = NarrationResult.model_validate(result).pages
                shorter = next(
                    (c.narration.strip() for c in pages if c.index == page.index), ""
                )
                made.append(ledger.text_artifact("压之前", draft.narration, page=page.index))
                if shorter:
                    made.append(ledger.text_artifact("压之后", shorter, page=page.index))
                # And what became of it, on the call that produced it. Written
                # here rather than as a record of its own: a separate line has
                # to repeat the page and the target to say which rewrite it is
                # talking about, and then floats in the middle of the run
                # belonging to nothing. Opened, this call already shows 压之前
                # and 压之后; 结果 is the third thing anyone looking at it wants.
                verdict, why = _keeps_enough(shorter, draft.narration, page, allowed)
                if verdict != "no" and not _worth_rewriting(shorter, draft.narration):
                    verdict = "no"
                    why = (
                        f"只短了 {len(draft.narration) - len(shorter)} 字，"
                        "这一轮在削字，不在少讲"
                    )
                made.append(
                    ledger.text_artifact(
                        "结果",
                        {
                            "ok": "采用了",
                            "costs": f"采用了，代价是{why}",
                            "no": f"没采用：{why}",
                        }[verdict],
                        page=page.index,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - a failed rewrite keeps the draft
            self.log.warning("第 %d 页压缩失败，保留原稿：%s", page.index, exc)
            return None

        for candidate in pages:
            if candidate.index != page.index or not candidate.narration.strip():
                continue
            if not _worth_rewriting(candidate.narration, draft.narration):
                continue
            # What the page is worth walking, and what the shorter length can
            # actually hold — whichever is less. Not 「不比原来少」: a draft that
            # already names exactly what the page is worth makes that its own
            # floor, and then no rewrite can ever be accepted, because dropping
            # something is what compressing a page *is*. Four of this deck's
            # eight rejected rewrites were rejected that way, and the pages
            # stayed twice their target length with the record saying they had
            # been compressed.
            verdict, why = _keeps_enough(candidate.narration, draft.narration, page, allowed)
            if verdict == "no":
                self.log.info("第 %d 页%s，不采用", page.index, why)
                continue
            if verdict == "costs":
                self.log.info("第 %d 页压缩的代价：%s", page.index, why)
            self.log.info(
                "第 %d 页改写压缩：%d → %d 字", page.index,
                len(draft.narration), len(candidate.narration),
            )
            return candidate
        return None

    def _placeholder(
        self,
        pages: list[DocumentPage],
        budgets: dict[int, float],
        kept: dict[int, str] | None = None,
    ) -> dict[int, PageNarration]:
        # No degradation recorded here: try_llm already logged one, with the
        # reason attached. A second record for the same event would double the
        # count that cross-run metrics compare.
        kept = kept or {}
        blanks = [page for page in pages if page.index not in kept]
        self.log.warning("使用占位讲稿（成片可渲染但内容无意义）：%d 页", len(blanks))
        drafts = {index: _as_draft(index, text) for index, text in kept.items()}
        drafts.update(self._write_heuristically(blanks, budgets))
        return drafts

    def apply(self, narrations: dict[int, str]) -> list[int]:
        """Adopt caller-written narration, keyed by page index.

        Returns the pages that had no text supplied — they fall back to the
        placeholder so a partial script still renders, and the caller can see
        exactly which pages it missed rather than discovering it in the video.
        """
        pages = self._pages()
        if not pages:
            return []

        budgets = self._allocate_budget(pages)
        # What this project already has. A page the caller did not mention is
        # not a page with nothing on it: the script may have been written last
        # week, or by 「生成讲稿」 five minutes ago. Overwriting it with
        # placeholder text threw away thirty pages of finished writing on a
        # call that said nothing about them — and the video that came out
        # opened with 「这一期我们来讲……」 over a script the model had already
        # written properly.
        existing = {
            scene.source_page: scene.narration
            for scene in self.project.scenes
            if scene.source_page and scene.narration.strip()
        }
        drafts: dict[int, PageNarration] = {}
        missing: list[int] = []
        for page in pages:
            text = (narrations.get(page.index) or existing.get(page.index) or "").strip()
            if text:
                drafts[page.index] = PageNarration(
                    index=page.index, narration=text, segments=[]
                )
            else:
                missing.append(page.index)
                drafts.update(self._write_heuristically([page], budgets))

        if missing:
            telemetry.record_degradation("讲稿", f"第 {missing} 页没有讲稿，使用占位文本")
            self.log.warning("这些页面没有讲稿，已用占位文本：%s", missing)

        self._build_scenes(pages, drafts)
        self.log.info(
            "讲稿已应用：%d 个场景，预计 %.1f 秒",
            len(self.project.scenes),
            self.project.total_duration(),
        )
        return missing

    def guide(self) -> list[dict]:
        """Per-page writing budget, for the caller to write against.

        Without this the caller is guessing at length, and a script that misses
        the requested duration cannot be fixed after the audio exists.

        ``target_seconds`` is time *spoken*; ``page_seconds`` is how long the
        page is on screen. They differ by the silence held at each end of a
        page, which is part of the video but not of the script.
        """
        pages = self._pages()
        budgets = self._allocate_budget(pages)
        return [
            {
                "page": page.index,
                "title": page.title,
                "page_type": page.page_type.value,
                "target_seconds": round(budgets[page.index], 1),
                "target_chars": self._char_budget(budgets[page.index]),
                "page_seconds": round(budgets[page.index] + self._page_silence(), 1),
            }
            for page in pages
        ]

    def _pages(self) -> list[DocumentPage]:
        skip = set(self.project.intent.skip_pages)
        pages = [p for p in self.project.document.ordered_pages() if p.index not in skip]
        if not pages:
            self.log.warning("没有可讲解的页面")
        return pages

    def rewrite_scene(self, scene: Scene, narration: str) -> None:
        """Replace one scene's script — the incremental-edit entry point.

        Only this scene changes, which is what keeps a "page 7 is too long" edit
        from re-voicing and re-rendering the whole video (方案 §9).
        """
        text = narration.strip()
        if not text:
            self.log.warning("场景 %s 的新讲稿为空，跳过", scene.scene_id)
            return
        scene.narration = text
        # Its own segments, so a sentence the edit left alone keeps what it was
        # pointing at. A one-page edit rewrites a paragraph, not usually every
        # sentence in it, and the untouched ones have no reason to lose the
        # binding the writer gave them.
        scene.segments = self._resplit(text, scene.scene_id, scene.segments)
        scene.duration = estimate_duration(
            text, self.ctx.settings.tts_speech_rate, self._pace()
        )

    def revise_scene(self, scene: Scene, instruction: str, target_seconds: float = 0.0) -> None:
        """Rewrite one scene from an instruction rather than from new text.

        This is what "第 3 页太长了，压到 20 秒" has to go through. The
        instruction is not narration — turning it into narration needs a model,
        and without one the honest outcome is to leave the scene alone and say
        so. Silently re-voicing the same words would report success for a run
        that changed nothing.
        """
        target = target_seconds or scene.duration
        budget = self._char_budget(max(target - self._page_silence(), MIN_SCENE_SECONDS))
        page = self.project.document.page(scene.source_page)

        def fallback() -> None:
            self.log.warning("没有模型，无法按「%s」改写场景 %s", instruction, scene.scene_id)

        def rewrite() -> None:
            with ledger.call(
                self.llm.source,
                f"改写 {scene.scene_id}",
                covers=[ledger.scene_key(scene.scene_id)],
            ):
                result = self.llm.complete_json(
                    self._revise_prompt(scene, page, instruction, budget),
                    schema=model_schema(PageNarration),
                    system=load_prompt("narration"),
                )
            revised = PageNarration.model_validate(result)
            if revised.narration.strip():
                self.rewrite_scene(scene, revised.narration.strip())

        self.try_llm(rewrite, fallback, what=f"修改第 {scene.source_page} 页")

    def _revise_prompt(
        self, scene: Scene, page: DocumentPage | None, instruction: str, budget: int
    ) -> str:
        lines = [
            f"# 修改第 {scene.source_page} 页的讲稿",
            f"用户的要求：{instruction}",
            f"改完后这一页的讲稿控制在 {budget} 字左右（正负 15%）。",
            "",
            "# 现在的讲稿",
            scene.narration,
        ]
        if page is not None:
            lines += ["", f"# 这一页的内容（标题：{page.title or '无'}）"]
            if page.key_points:
                lines.append("关键点：" + "；".join(page.key_points))
            lines.extend(
                f"  - {e.id}｜{e.kind.value}｜{_truncate(e.text, 120)}"
                for e in page.elements
                if e.text
            )
        lines += ["", f"只返回这一页，index 用 {scene.source_page}。"]
        return "\n".join(lines)

    def _page_silence(self) -> float:
        """Seconds each page holds without speech, at its head and its tail."""
        return tuning.value("voice.lead", self.ctx.settings) + tuning.value(
            "voice.tail", self.ctx.settings
        )

    def _allocate_budget(self, pages: list[DocumentPage]) -> dict[int, float]:
        """Seconds of *speech* per page, summing to the requested duration.

        The silence around each page is a fixed cost that has to come off the
        top: it is on screen but nobody is talking, so budgeting it as writing
        time would make every page's script overrun by that much — a 16-page
        deck would run 24 seconds long against its target.
        """
        intent = self.project.intent
        silence = self._page_silence() * len(pages)
        # Never let the reservation eat the whole request: a very short target
        # on a long deck should still produce a script, just a terse one.
        speech_total = max(intent.duration - silence, intent.duration * 0.4)
        weights: dict[int, float] = {}
        for page in pages:
            # How much there is to say, which is now mostly how much the page
            # says: the script reads the deck rather than commenting on it, so
            # a page's share of the film should track its own text. The old
            # factor (1.0–2.0 over the first 600 characters) left the type
            # weight in charge, and it showed — a 1122-character page and a
            # 112-character cover were budgeted 78 and 19 characters, so the
            # dense page could not be read out and the cover had four times
            # its own text to fill. Measured on that deck: pages ran from 7%
            # to 405% of their budget.
            #
            # What this page is worth saying, which is not the same as how much
            # text it carries: a 24-block page is chosen from rather than read
            # out, so its share is the share of the blocks that get named.
            # Weighting by raw characters instead left the writer a budget the
            # size of the page and it filled it — 46 characters an item where
            # the estimate says 18, and a film 20.7 minutes long against the
            # 12.5 the same estimate had proposed.
            weight = TYPE_WEIGHT.get(page.page_type, 1.0) ** TYPE_INFLUENCE * page_share_chars(
                page
            )
            if page.index in intent.emphasis_pages:
                weight *= EMPHASIS_MULTIPLIER
            weights[page.index] = weight

        # A page's floor is what reading its own words costs. A cover carrying
        # 112 characters was budgeted 19 and came back with 80 — the model was
        # right and the budget was wrong, and the same happened on every sparse
        # page: 2–4× over, measured across seven of them. Proportional shares
        # alone cannot fix it, because these pages are small in proportion and
        # still have a floor of their own text.
        pace = self._pace()
        floors = {
            page.index: min(
                max(len(page.raw_text()) / pace, MIN_SCENE_SECONDS), MAX_FLOOR_SECONDS
            )
            for page in pages
        }
        # The floors are a claim on the film, and thirty pages' worth of them
        # can exceed the whole thing — this deck's came to 625 seconds against
        # a 360-second target, which would have made the length the user asked
        # for meaningless. Above their share they are scaled down together, so
        # they stay in proportion to each other and the target still holds. On
        # a deck that dense some of the text goes unread whatever we do; §三 of
        # the writing prompt says to drop whole items rather than compress them
        # all into summary.
        claimed = sum(floors.values())
        cap = speech_total * FLOOR_SHARE
        if claimed > cap:
            # Only the part above the minimum is scaled. Scaling the whole
            # floor put a 20-character page at 1.4 seconds of speech — a scene
            # shorter than the silence around it, which is not a scene.
            base = MIN_SCENE_SECONDS * len(pages)
            room = max(claimed - base, 1e-6)
            scale = max(cap - base, 0.0) / room
            floors = {
                index: MIN_SCENE_SECONDS + (seconds - MIN_SCENE_SECONDS) * scale
                for index, seconds in floors.items()
            }
        return self._share(speech_total, weights, floors)

    @staticmethod
    def _share(
        total: float, weights: dict[int, float], floors: dict[int, float]
    ) -> dict[int, float]:
        """Split `total` by weight, but never below a page's floor.

        Pages that would fall under their floor are fixed at it and taken out
        of the pool; the rest re-divide what remains, which can push another
        page under its own floor — hence the loop. It ends because each pass
        fixes at least one page, and if every page is fixed there is nothing
        left to divide.
        """
        budgets: dict[int, float] = {}
        pool, open_pages = total, dict(weights)
        while open_pages:
            weight_sum = sum(open_pages.values()) or 1.0
            under = {
                index: floors[index]
                for index, weight in open_pages.items()
                if pool * weight / weight_sum < floors[index]
            }
            if not under:
                for index, weight in open_pages.items():
                    budgets[index] = min(MAX_SCENE_SECONDS, pool * weight / weight_sum)
                break
            budgets.update(under)
            pool -= sum(under.values())
            for index in under:
                open_pages.pop(index)
            if pool <= 0:
                # The floors alone spend the whole target. Everyone left gets
                # theirs anyway — a page still has to say its own title — and
                # the film runs long, which the run reports.
                budgets.update({index: floors[index] for index in open_pages})
                break
        return budgets

    def _char_budget(self, seconds: float) -> int:
        """How many characters fit in `seconds`, for the engine that will speak them.

        The number used to be written here as well as in the estimator, and the
        two were the same until they were not: a page budgeted against 4.6
        characters a second and spoken at 4.15 runs eleven percent long before
        the model has done anything wrong. One source now, and it follows the
        voice — `say` says 4.75, Edge's broadcast voice 4.15.
        """
        # The engine's own pace times what it was asked to do with it: the pace
        # is measured at 1.0, and a deck spoken 5% quicker fits 5% more script
        # in the same seconds.
        return int(seconds * self._pace() * max(self.ctx.settings.tts_speech_rate or 1.0, 0.1))

    def _pace(self) -> float:
        from ..tools.tts import TTSTool

        return TTSTool(self.ctx.settings).chars_per_second

    # -- model path -----------------------------------------------------
    def _write_with_model(
        self,
        pages: list[DocumentPage],
        budgets: dict[int, float],
        kept: dict[int, str] | None = None,
        *,
        progress: ProgressFn | None = None,
    ) -> dict[int, PageNarration]:
        """Write the whole deck in batches.

        Batched rather than per-page because a script's job is to connect: a
        page written without sight of its neighbours opens with a transition
        from nothing. Batched rather than all-at-once because a long deck
        overruns the output budget, and a truncated reply loses whole pages.

        Pages in ``kept`` are already written. They stay in their batch — that
        is how the model sees what the page before and after actually says —
        but they are asked for back. A batch where every page is written is
        skipped entirely, which is what makes "fill in the three I left blank"
        cost three pages of writing rather than thirty.

        Whatever the model does not return is filled in heuristically. A model
        that skips page 7 must not cost page 7 its narration.
        """
        kept = kept or {}
        drafts: dict[int, PageNarration] = {
            index: _as_draft(index, text) for index, text in kept.items()
        }
        wanted_batches = [
            (start, pages[start : start + BATCH_SIZE])
            for start in range(0, len(pages), BATCH_SIZE)
            if not all(page.index in kept for page in pages[start : start + BATCH_SIZE])
        ]

        # Batches do not see each other. Each is given its own pages and the
        # ones a person already wrote; nothing a batch produces reaches the
        # next one. So writing them one after another bought no continuity —
        # it only spent fifteen minutes of a forty-minute film waiting.
        #
        # Kept small on purpose. Measured at 59s against 37s for two batches,
        # which is worth having; the ceiling is not the machine but whatever
        # the model is behind, and one transient failure while probing this was
        # reminder enough that asking for more is asking for a rate limit.
        workers = writing_workers(len(wanted_batches), self.ctx.settings.narrate_workers)
        if workers > 1:
            self.log.info("写 %d 批，%d 批一起写", len(wanted_batches), workers)
            self._write_together(wanted_batches, drafts, budgets, kept, pages, workers, progress)
            return self._filled_in(drafts, pages, budgets, kept)

        for start, batch in wanted_batches:
            # Said before the call, not after. A batch is one model call and
            # takes a minute or more; a count that only moves when a call
            # returns sits still for that whole minute, and the window has no
            # way to say which pages are being written *now* — which is the
            # one thing the person waiting wants to know.
            if progress is not None:
                progress(
                    "narrate",
                    f"第 {batch[0].index}-{batch[-1].index} 页",
                    len(drafts),
                    len(pages),
                )
            with ledger.call(
                self.llm.source,
                f"第 {batch[0].index}-{batch[-1].index} 页",
                covers=[ledger.page_key(page.index) for page in batch if page.index not in kept],
            ) as made:
                result = self.llm.complete_json(
                    self._prompt(batch, budgets, position=start, kept=kept, pages=pages),
                    schema=model_schema(NarrationResult),
                    system=load_prompt("narration"),
                    max_tokens=self.ctx.settings.llm_max_tokens,
                )
                answered = list(NarrationResult.model_validate(result).pages)
                by_index = {p.index: p for p in answered}
                if note := _binding_note(batch, by_index):
                    made.append(ledger.text_artifact("说了讲的是哪一处", note))
                blind = [p.index for p in batch if _binds_nothing(by_index.get(p.index))]
                if blind:
                    ledger.degradation(
                        "讲稿没绑元素",
                        f"第 {'、'.join(str(i) for i in blind)} 页｜镜头只能靠字面猜",
                    )
            for page in answered:
                # A model that rewrites a page it was told to leave alone gets
                # ignored rather than obeyed.
                if page.narration.strip() and page.index not in kept:
                    drafts[page.index] = page
            # Save what has been written so far. A deck takes several calls to
            # write and each one is a wait; a reader watching the pages fill in
            # is being told the truth about where it has got to, where a blank
            # list until the last batch lands is not.
            self._build_scenes(pages, drafts)
            self.ctx.store.save(self.project)

        return self._filled_in(drafts, pages, budgets, kept)

    def _write_together(
        self,
        batches: list[tuple[int, list[DocumentPage]]],
        drafts: dict[int, PageNarration],
        budgets: dict[int, float],
        kept: dict[int, str],
        pages: list[DocumentPage],
        workers: int,
        progress: ProgressFn | None,
    ) -> None:
        """Write several batches at once, saving each as it lands.

        The account of a run is kept on a context variable, so a batch written
        in a thread that does not carry the calling context records its call
        nowhere. Each task runs inside a copy of the context it came from.

        Saving stays on this thread. Two batches writing the project file at
        the same moment is a corrupted file, and the point of saving early is
        that a reader can watch the pages fill in — which one writer at a time
        does perfectly well.
        """
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for start, batch in batches:
                carried = copy_context()
                futures[
                    pool.submit(carried.run, self._write_batch, batch, budgets, start, kept, pages)
                ] = batch
            for future in as_completed(futures):
                batch = futures[future]
                done += 1
                try:
                    written = future.result()
                except Exception:
                    # One batch's failure costs those pages their model draft,
                    # not the deck: the heuristic writer fills them below.
                    self.log.exception("第 %d-%d 页写作失败", batch[0].index, batch[-1].index)
                    continue
                for page in written:
                    if page.narration.strip() and page.index not in kept:
                        drafts[page.index] = page
                # Said after the pages land, not before the call: several
                # batches are in flight at once, so 「正在写第 5-8 页」 would be
                # one of three true answers. How many pages exist now is the
                # one answer that is not a guess.
                if progress is not None:
                    progress(
                        "narrate",
                        f"第 {batch[0].index}-{batch[-1].index} 页",
                        len(drafts),
                        len(pages),
                    )
                self._build_scenes(pages, drafts)
                self.ctx.store.save(self.project)

    def _write_batch(
        self,
        batch: list[DocumentPage],
        budgets: dict[int, float],
        start: int,
        kept: dict[int, str],
        pages: list[DocumentPage],
    ) -> list[PageNarration]:
        """One batch, asked for and validated. Nothing here touches the project."""
        with ledger.call(
            self.llm.source,
            f"第 {batch[0].index}-{batch[-1].index} 页",
            covers=[ledger.page_key(page.index) for page in batch if page.index not in kept],
        ) as made:
            result = self.llm.complete_json(
                self._prompt(batch, budgets, position=start, kept=kept, pages=pages),
                schema=model_schema(NarrationResult),
                system=load_prompt("narration"),
                max_tokens=self.ctx.settings.llm_max_tokens,
            )
            written = list(NarrationResult.model_validate(result).pages)
            by_index = {p.index: p for p in written}
            if note := _binding_note(batch, by_index):
                made.append(ledger.text_artifact("说了讲的是哪一处", note))
            blind = [p.index for p in batch if _binds_nothing(by_index.get(p.index))]
            if blind:
                ledger.degradation(
                    "讲稿没绑元素",
                    f"第 {'、'.join(str(i) for i in blind)} 页｜镜头只能靠字面猜",
                )
        return written

    def _filled_in(
        self,
        drafts: dict[int, PageNarration],
        pages: list[DocumentPage],
        budgets: dict[int, float],
        kept: dict[int, str],
    ) -> dict[int, PageNarration]:
        """Whatever the model did not write, written heuristically."""
        wanted = {p.index for p in pages}
        missing = sorted(wanted - drafts.keys())
        if missing:
            telemetry.record_degradation("讲稿", f"模型漏掉第 {missing} 页，改用占位文本")
            self.log.warning("模型未覆盖 %s，这几页使用占位讲稿", missing)
            drafts.update(
                self._write_heuristically([p for p in pages if p.index in set(missing)], budgets)
            )
        return {index: drafts[index] for index in sorted(wanted)}

    def _prompt(
        self,
        batch: list[DocumentPage],
        budgets: dict[int, float],
        *,
        position: int,
        kept: dict[int, str] | None = None,
        pages: list[DocumentPage] | None = None,
    ) -> str:
        document = self.project.document
        intent = self.project.intent
        lines = [
            f"# 文档：{document.title or '未命名'}",
            f"主题：{document.topic or '未知'}",
            f"整体摘要：{document.summary or '（无）'}",
            "",
            f"# 观众与风格\n受众：{intent.audience}\n"
            f"风格：{STYLE_BRIEF.get(intent.style, intent.style)}\n"
            f"语气：{intent.tone}\n"
            f"额外要求：{intent.instructions or '（无）'}",
            "",
        ]
        kept = kept or {}
        if kept:
            lines += [
                "# 这次的任务：补齐没写的页面",
                "下面标了「已写好」的页面是人自己写的。原样保留，不要改写、不要润色，"
                "也不要在结果里输出它们。你要写的是标了「待补写」的页面，"
                "并且要跟前后已写好的内容接得上：不重复它们讲过的话，"
                "承接它们的说法和称呼，转场自然。",
                "",
            ]
            if before := self._nearest_written(pages, kept, batch[0].index, back=True):
                lines += [f"# 上文（第 {before[0]} 页，已写好，结尾）", _tail(before[1], 200), ""]
            if after := self._nearest_written(pages, kept, batch[-1].index, back=False):
                lines += [f"# 下文（第 {after[0]} 页，已写好，开头）", _truncate(after[1], 200), ""]
        lines.append(f"# 页面（全文第 {position + 1} 页起，共 {len(budgets)} 页）")
        for page in batch:
            mark = "已写好" if page.index in kept else ("待补写" if kept else "")
            lines.append(
                f"\n## 第 {page.index} 页｜{page.page_type.value}｜{page.title or '无标题'}"
                + (f"｜{mark}" if mark else "")
            )
            if text := kept.get(page.index):
                lines.append(f"已写好的讲稿：{text}")
                continue
            if axes := _matrix_of(page):
                across, down = axes
                lines.append(
                    "这一页是一张表：横着分 "
                    + "、".join(across)
                    + "；竖着分 "
                    + "、".join(down)
                    + "。"
                    "先把这两条分法各自说清楚——横的是按什么分的，竖的是按什么分的——"
                    "再举例子。举例子时要让人听得出这一项落在哪一格：说「创新链上的核心专利」"
                    "而不是只说「核心专利」。"
                    "最忌讳的是把不同格子里的条目并成一串念，那样两条分法就都没了。"
                )
            if flow := _flow_of(page):
                # The order the arrows go, which is the order the page has to
                # be explained in. Given to the writer rather than used to
                # reorder the camera: a shot is bound to the sentence that
                # mentions it, and pointing at a box the current sentence is
                # not talking about is worse than crossing the diagram out of
                # order (方案 §20).
                lines.append(f"这一页画的是一条流程，按箭头走是：{flow}")
            # A ceiling, said as one. It used to be a target with a remedy
            # attached — 「不够时补原因、影响、数字背后的含义」 — which is the
            # right instruction for a script that explains the deck and the
            # wrong one for a script that reads it: the only way to reach a
            # number the page has no words for is to invent some.
            budget = self._char_budget(budgets[page.index])
            # Stated as a target with both failure modes named, because a
            # ceiling alone does not work: the model writes about one paragraph
            # a page whatever the number says. Measured across 30 pages — dense
            # pages came in at 38–60% of budget while sparse ones ran 200–286%,
            # so the page with 582 characters on it and the page with 79 got
            # narration of the same length. Each direction has one likely
            # cause, and saying which is what makes the number actionable.
            budget = self._char_budget(budgets[page.index])
            low, high = int(budget * 0.8), int(budget * 1.3)
            lines.append(
                f"字数预算：{budget} 字（{low}–{high} 字）。"
                f"这一页页面文字 {len(page.raw_text())} 字，预算是按它算的。"
                "写少了通常是漏讲了页面上的内容——回去把具体的条目、数字、名称补上；"
                "写多了通常是在发挥——删掉页面支撑不了的话。"
                "报了数就要点名：说了「三块」「四类」，就要把这几项的名字说出来，"
                "装不下就别报数。"
            )
            if note := _density_note(page, budget):
                lines.append(note)
            # The page's own words come first and are named as the material.
            # They used to come last, under 「元素：」, after a summary and a
            # key-point list the understanding step had written — so the first
            # thing the writer read was already a summary of the page, and it
            # wrote a summary of that. 「之前的讲稿太总结了」 has a cause, and
            # this is most of it.
            elements = [e for e in page.elements if e.text]
            if elements:
                # 「按这个顺序讲」 is right for a content page and wrong for a
                # cover: it is the line that made a cover read its four lines
                # out one at a time, date included, over the top of its own
                # instruction not to. A cover's lines are typesetting.
                if page.page_type is PageType.COVER:
                    lines.append(
                        f"页面原文（{len(elements)} 处）——这是排版不是句子，"
                        "说成一到两句话：谁做的、做的是什么。"
                        "日期和联系方式不用念出来。"
                        # 「单位后缀」 used to be on that list, and it is the
                        # reason 「宁波城知产业链数据科技有限公司」 was read as
                        # 「宁波城知产业链数据科技」. A name shortened is a
                        # different name — and on a cover the name is most of
                        # what the page is for.
                        "机构名照页面写全，不要削掉「有限公司」「中心」「研究院」"
                        "这类后缀，也不要简称："
                    )
                else:
                    lines.append(
                        f"页面原文（{len(elements)} 处，讲稿的主要材料，按这个顺序讲）："
                    )
                # Longer than it was: this is the source text now, not a hint
                # about what the page is roughly about. A body paragraph cut at
                # 120 characters is a paragraph the narration cannot read out,
                # and the model fills the gap the only way it can — by making
                # something up.
                lines.extend(
                    f"  - {e.id}｜{e.kind.value}｜{_truncate(e.text, 400)}" for e in elements
                )
            if page.speaker_notes:
                lines.append(f"演讲者备注：{_truncate(page.speaker_notes, 300)}")
            if page.summary or page.key_points:
                lines.append("（以下是这一页的理解笔记，帮你判断轻重，不要照着它写——它已经是概括了）")
            if page.summary:
                lines.append(f"页面摘要：{page.summary}")
            if page.key_points:
                lines.append("关键点：" + "；".join(page.key_points))
        return "\n".join(lines)

    @staticmethod
    def _nearest_written(
        pages: list[DocumentPage] | None,
        kept: dict[int, str],
        edge: int,
        *,
        back: bool,
    ) -> tuple[int, str] | None:
        """The written page just outside this batch, on one side.

        The batch itself carries most of the continuity. This carries the rest:
        the page before the batch and the page after it are what the first and
        last pages of the batch have to join onto, and they are usually in a
        different call.
        """
        order = [page.index for page in (pages or [])]
        candidates = [i for i in order if (i < edge if back else i > edge) and i in kept]
        if not candidates:
            return None
        index = max(candidates) if back else min(candidates)
        return index, kept[index]

    # -- heuristic path -------------------------------------------------
    def _write_heuristically(
        self, pages: list[DocumentPage], budgets: dict[int, float]
    ) -> dict[int, PageNarration]:
        drafts: dict[int, PageNarration] = {}
        for order, page in enumerate(pages):
            sentences = self._heuristic_sentences(page, order, budgets[page.index])
            segments = [
                SegmentDraft(
                    text=text,
                    element_refs=refs,
                    emphasis=bool(refs) and idx > 0,
                )
                for idx, (text, refs) in enumerate(sentences)
            ]
            drafts[page.index] = PageNarration(
                index=page.index,
                narration="".join(s.text for s in segments),
                segments=segments,
            )
        return drafts

    def _heuristic_sentences(
        self, page: DocumentPage, order: int, seconds: float
    ) -> list[tuple[str, list[str]]]:
        title = page.title or f"第 {page.index} 页"
        sentences: list[tuple[str, list[str]]] = []

        if page.page_type is PageType.COVER:
            topic = self.project.document.topic or title
            sentences.append((f"这一期我们来讲{topic}。", []))
        else:
            sentences.append((TRANSITIONS[order % len(TRANSITIONS)].format(title=title), []))

        ranked = sorted(page.elements, key=lambda e: -e.importance)
        budget_chars = self._char_budget(seconds)
        used = sum(len(text) for text, _ in sentences)

        for point in page.key_points:
            if used >= budget_chars:
                break
            target = next(
                (e.id for e in ranked if point[:8] and point[:8] in e.text),
                ranked[0].id if ranked else None,
            )
            text = f"{point}。"
            sentences.append((text, [target] if target else []))
            used += len(text)

        # Speaker notes are the closest thing a deck has to an explanation of
        # itself — use them to fill the remaining budget before padding.
        if used < budget_chars and page.speaker_notes:
            note = page.speaker_notes.strip().replace("\n", "")[: budget_chars - used]
            if len(note) >= 6:
                sentences.append((f"{note}。", []))
                used += len(note)

        if len(sentences) == 1 and page.summary:
            sentences.append((f"{page.summary}。", []))
        return sentences

    # -- scene assembly -------------------------------------------------
    def _build_scenes(self, pages: list[DocumentPage], drafts: dict[int, PageNarration]) -> None:
        # What this project already knew about its own sentences, before the
        # rebuild overwrites them. Three paths arrive here with a page's text
        # and no segments — `apply` takes `dict[int, str]` and has nowhere to
        # put them, `_trim_to` returns a bare string, and hand-written pages
        # never had any — and each one used to fall through to the splitter,
        # which builds segments with an empty `element_refs`.
        #
        # That is the whole of 「框选始终有问题」. Measured across six real
        # projects the field is bimodal, 0% or 98%, never in between: the decks
        # written by `run` came back bound, and every deck that passed through
        # `apply` — a re-render of a finished script included — arrived at the
        # director with 158 of 158 sentences saying 「I am about nothing」. The
        # director treats a bound page as authoritative and an unbound one as
        # something to guess (`_targets_in`), so a script that knew exactly
        # which line each sentence came from had the camera reverse-engineering
        # it out of shared characters anyway.
        #
        # The text is the key because the text is what survives: a trim keeps
        # whole sentences off the front, and a re-apply usually carries the
        # very same string back in. A sentence that changed finds nothing and
        # is guessed at, which is the right answer for a sentence nobody has
        # bound yet.
        known = {
            scene.source_page: [
                segment for segment in scene.segments if segment.text.strip()
            ]
            for scene in self.project.scenes
            if scene.source_page
        }
        scenes: list[Scene] = []
        for order, page in enumerate(pages, start=1):
            draft = drafts.get(page.index)
            if draft is None:
                continue
            valid_ids = {e.id for e in page.elements}
            segments: list[NarrationSegment] = []
            for seq, seg in enumerate(draft.segments, start=1):
                text = seg.text.strip()
                if not text:
                    continue
                # Drop refs the model invented; a wrong id would aim the camera
                # at nothing.
                refs = [ref for ref in seg.element_refs if ref in valid_ids]
                segments.append(
                    NarrationSegment(
                        id=f"{scene_id(order)}_s{seq:02d}",
                        text=text,
                        element_refs=refs,
                        emphasis=seg.emphasis,
                    )
                )
            if not segments:
                segments = self._resplit(
                    draft.narration, scene_id(order), known.get(page.index)
                )

            narration = draft.narration.strip() or "".join(s.text for s in segments)
            scenes.append(
                Scene(
                    scene_id=scene_id(order),
                    source_page=page.index,
                    title=page.title,
                    narration=narration,
                    segments=segments,
                    duration=estimate_duration(
                        narration, self.ctx.settings.tts_speech_rate, self._pace()
                    ),
                    visual=SceneVisual(
                        type=VisualType.SLIDE,
                        asset=page.image_path,
                        source_page=page.index,
                    ),
                )
            )
        self.project.scenes = scenes

    @classmethod
    def _resplit(
        cls, text: str, prefix: str, before: list[NarrationSegment] | None = None
    ) -> list[NarrationSegment]:
        """Cut text into segments, keeping whatever was known about them.

        ``before`` is this page's previous segments. Two ways they help, and
        the first is the one that matters:

        * **The text did not change.** Then re-cutting it is not just lossy,
          it is wrong: the writer chose where its sentences ended, and the
          splitter does not reproduce that choice — a segment holding two
          sentences comes back as two. On a real 30-page deck that alone moved
          162 segments to 168 and dropped thirteen bindings that had matched
          fine. So the old segments are kept whole, renumbered and nothing
          else. Segments are what TTS times and what the camera follows; there
          is no reason for adopting a script to re-decide either.
        * **Some of it changed.** Then the sentences that came back word for
          word keep what the writer said they pointed at, and keep their
          emphasis. Both are answers given with the page's element list in
          view, and neither survives the string once it is dropped.
        """
        before = before or []
        joined = "".join(segment.text for segment in before).strip()
        if before and joined == text.strip():
            return [
                segment.model_copy(update={"id": f"{prefix}_s{i:02d}"})
                for i, segment in enumerate(before, start=1)
            ]
        known = {segment.text.strip(): segment for segment in before}
        parts = [p.strip() for p in SENTENCE_SPLIT.split(text) if p.strip()]
        segments: list[NarrationSegment] = []
        for i, part in enumerate(parts, start=1):
            was = known.get(part)
            segments.append(
                NarrationSegment(
                    id=f"{prefix}_s{i:02d}",
                    text=part,
                    element_refs=list(was.element_refs) if was else [],
                    emphasis=was.emphasis if was else False,
                )
            )
        return segments


def _as_draft(index: int, text: str) -> PageNarration:
    """Hand-written text as a draft. Segments are cut later, by the same
    splitter the model's own text goes through — there is no reason for a page
    someone typed to be segmented differently from one that was generated."""
    return PageNarration(index=index, narration=text, segments=[])


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def _truncate(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _flow_of(page: DocumentPage) -> str:
    """A page's declared flow as words, or empty when it declares none."""
    if page.diagram is None:
        return ""
    labels = {e.id: (e.text or e.label or e.id).strip() for e in page.elements}
    walked = [labels.get(node, node) for node in page.diagram.order()]
    return " → ".join(name for name in walked if name)


#: How many headings a row or a column has to have before it is an axis.
AXIS_HEADINGS = 3

#: How much of the page an axis has to span, as a share of its length. A row of
#: three headings crowded into one corner is a list, not the top of a table.
AXIS_SPAN = 0.4

#: A heading is short. Past this it is the content itself.
AXIS_LABEL_CHARS = 20





def _binds_nothing(draft) -> bool:
    """Whether a page came back with no sentence saying what it is about."""
    if draft is None or not draft.segments:
        return False
    return not any(seg.element_refs for seg in draft.segments)


def _binding_note(pages, written) -> str:
    """How much of what came back said which element it was about.

    Counted because this failed silently for a long time: the writer is asked
    which element each sentence is talking about and the camera reads the
    answer before it tries to guess, and on a real 30-page film every one of
    155 sentences came back with an empty list while nothing said so.

    Said on the call that wrote the page, not as a record of its own. Written
    where the scenes are built it was said again on every rebuild — and the
    scenes are rebuilt after every batch so the pages can be watched filling
    in, so one page reported itself five times over.
    """
    lines = []
    for page in pages:
        draft = written.get(page.index)
        if draft is None or not draft.segments:
            continue
        valid = {e.id for e in page.elements}
        asked = len(draft.segments)
        bound = sum(1 for seg in draft.segments if any(r in valid for r in seg.element_refs))
        invented = sum(
            len([r for r in seg.element_refs if r not in valid]) for seg in draft.segments
        )
        said = f"第 {page.index} 页 {bound}/{asked} 句"
        if invented:
            said += f"（{invented} 个 id 页面上没有）"
        lines.append(said)
    return "；".join(lines)


def _worth_rewriting(candidate: str, draft: str) -> bool:
    """Whether a rewrite took enough off the page to be worth keeping."""
    return bool(candidate) and len(candidate) <= len(draft) * (1 - WORTH_ANOTHER_ROUND)


def _keeps_enough(candidate: str, draft: str, page, allowed: int) -> tuple[str, str]:
    """Whether a shortened page still says enough of the page to be used.

    Returns `("ok"|"costs"|"no", why)`.

    The floor used to be 「不比原来少」 — `min(what the draft named, what the page
    is worth)`. On a page where the draft already names exactly what the page is
    worth, that makes the draft its own floor, and then no rewrite can ever be
    accepted, because dropping something is what compressing a page *is*. Four
    of one deck's eight rejected rewrites were rejected that way, and those
    pages stayed at twice their target length while the record said they had
    been compressed. It took someone reading the 「压之后」 to notice.

    So the floor is what the page is worth walking, capped by what the shorter
    length can hold. And one below it is allowed — recorded as a degradation,
    not done quietly — when the page is far enough past its length that one
    item is the cheaper loss. A page whose blocks this measure cannot match
    names nothing either way, and a floor there is a number about nothing.
    """
    from .review import _dangling_counts, missed_items, worth_naming

    # A named list turned back into a bare count is never worth it: the rewrite
    # was asked for fewer words, and 「五部分」 followed by two of them is exactly
    # the shape that gets noticed.
    if _dangling_counts(candidate) and not _dangling_counts(draft):
        return "no", "改写后报了数不点名"

    named = missed_items(candidate, page)[0]
    walked = missed_items(draft, page)[0]
    if walked == 0:
        return "ok", ""
    floor = max(1, min(worth_naming(page), allowed // MIN_ITEM_CHARS_FOR_FLOOR))
    if named >= floor:
        return "ok", ""
    if named >= floor - 1 and len(draft) > allowed * KEEP_COMPRESSING:
        return "costs", f"{walked} 处减到 {named} 处，为压到 {allowed} 字"
    return "no", f"改写后只讲到 {named} 处，短到 {allowed} 字也该讲 {floor} 处"


def _matrix_of(page: DocumentPage) -> tuple[list[str], list[str]] | None:
    """A page's two axes, when it is laid out as a table.

    Some pages are a grid: 「(一)高质量数据集」 is four chains across the top and
    four kinds of dataset down the side, with sixteen cells of bullets between
    them. The writer is handed the page as a flat list in reading order and
    cannot see that, so it wrote the cells out one after another — 「预训练数据集，
    比如核心专利与科研成果、招投标商机…」 — which takes two items from one column
    and two from another and reads as one list. The page's second axis is gone,
    and with it what any of those items belongs to. 「表述不太准确。」

    Found geometrically, because that is where it is: a row of short headings
    spread across the page, a column of short headings down its side, and
    content between them.
    """
    if not page.width or not page.height:
        return None
    short = [
        element
        for element in page.elements
        if element.bbox is not None
        and 0 < len((element.text or "").strip()) <= AXIS_LABEL_CHARS
    ]
    if len(short) < AXIS_HEADINGS * 2:
        return None

    def clustered(items, key, tolerance: float) -> list[list]:
        groups: list[list] = []
        for item in sorted(items, key=key):
            if groups and abs(key(item) - key(groups[-1][-1])) <= tolerance:
                groups[-1].append(item)
            else:
                groups.append([item])
        return groups

    # Across the top: one line, several headings, spread wide.
    rows = clustered(short, lambda e: e.bbox.y, page.height * 0.02)
    across = max(
        (
            group
            for group in rows
            if len(group) >= AXIS_HEADINGS
            and (max(e.bbox.x + e.bbox.w for e in group) - min(e.bbox.x for e in group))
            >= page.width * AXIS_SPAN
        ),
        key=len,
        default=None,
    )
    # Down the side: one left edge, several headings, spread tall.
    columns = clustered(short, lambda e: e.bbox.x, page.width * 0.02)
    down = max(
        (
            group
            for group in columns
            if len(group) >= AXIS_HEADINGS
            and (max(e.bbox.y + e.bbox.h for e in group) - min(e.bbox.y for e in group))
            >= page.height * AXIS_SPAN
        ),
        key=len,
        default=None,
    )
    if across is None or down is None or {e.id for e in across} & {e.id for e in down}:
        return None

    # And something in the cells. A bare pair of axes with nothing between them
    # is two lists that happen to be perpendicular.
    named = {e.id for e in across} | {e.id for e in down}
    cells = sum(
        1
        for element in page.elements
        if element.id not in named and (element.text or "").strip()
    )
    if cells < len(across):
        return None

    return (
        [(e.text or "").strip() for e in sorted(across, key=lambda e: e.bbox.x)],
        [(e.text or "").strip() for e in sorted(down, key=lambda e: e.bbox.y)],
    )


def _trim_to(text: str, limit: int) -> str:
    """Drop whole sentences from the end until the text fits.

    Sentences rather than characters: cutting mid-sentence leaves the narrator
    stopping in the middle of a thought, which is worse than the page being
    long. The first sentence is always kept — a page that says nothing is not
    a shorter page, it is a missing one.
    """
    if len(text) <= limit:
        return text
    parts = [part for part in re.split(r"(?<=[。！？])", text) if part.strip()]
    if len(parts) <= 1:
        return text
    kept = [parts[0]]
    for part in parts[1:]:
        if len("".join(kept)) + len(part) > limit:
            break
        kept.append(part)
    return "".join(kept)
