"""What the engine is told to say, when that differs from what is written.

A caption reads 「RAG 模块」 and a narrator says "R-A-G 模块". Both are right,
and the strings are not the same — so the text handed to the synthesiser is
not the text put on screen. Getting this backwards is audible: one wrong term
makes a whole video sound cheap.
"""

from __future__ import annotations

from doc2video.agent.planner import parse_intent_rules
from doc2video.schemas import VideoIntent
from doc2video.tools.tts.pronounce import for_speech


def test_an_initialism_read_as_a_word_is_spelled_out():
    """Measured, not assumed: `say` reads RAG as "rag", 0.49s against 0.74s."""
    assert for_speech("这里最关键的是 RAG 模块。") == "这里最关键的是 R A G 模块。"


def test_a_short_run_of_capitals_is_read_as_letters():
    """It said 「AI」 as a word, and 「CCAI」 as something like a word.

    This test used to assert the opposite — that `say` spells initialisms out
    on its own and the list should stay small. Listening says otherwise, and
    so does the clock: 「CCAI」 comes back in 0.40 seconds where its four
    letters take 1.01.

    Length is the line. Two to four capitals are an initialism; five and up is
    a word set in capitals, and 「SKILL.md」 is a file name rather than
    S-K-I-L-L.
    """
    # Written as the letters' names, not as the letters: handed two lone Latin
    # characters, a Chinese voice read 「A」 as 啊 — an interjection.
    # Only the letters a Chinese voice mis-reads on their own are written as
    # syllables: 「A」 came back as 啊. 「C」 is better left as the letter — 「西」
    # is a Chinese word that merely sounds near it.
    assert for_speech("AI 应用中试平台") == "诶爱 应用中试平台"
    assert for_speech("浙江大学 CCAI 宁波中心") == "浙江大学 CC诶爱 宁波中心"
    # All consonants: nothing to rewrite, and nothing to separate either —
    # the engine reads MCP as its letters on its own.
    assert for_speech("石化生态 MCP 工具库") == "石化生态 MCP 工具库"
    assert for_speech("依托 SKILL.md 规范") == "依托 SKILL.md 规范"
    # Words that happen to be in capitals stay words: a deck's 「MAIL」 is mail.
    assert for_speech("MAIL 收件箱") == "MAIL 收件箱"
    # And SaaS is said as a word by people, so spelling it out would be worse.
    assert for_speech("SaaS 产品") == "SaaS 产品"



def test_a_project_can_overrule_the_built_in_list():
    """A deck about a company called RAG is talking about the company."""
    assert for_speech("RAG 是我们的名字", {"RAG": "RAG"}) == "RAG 是我们的名字"


def test_the_pronunciation_can_be_said_in_one_sentence():
    """The same door everything else in this product goes through."""
    intent = parse_intent_rules("QPS 读作 Q P S，语速慢一点", VideoIntent())
    assert intent.pronunciation == {"QPS": "Q P S"}
    assert 0 < intent.speech_rate < 1

    # Narrow on purpose: a loose pattern would rewrite words that were fine.
    assert parse_intent_rules("这一页讲得再细一点", VideoIntent()).pronunciation == {}


def test_the_caption_keeps_the_written_form(settings, tmp_path):
    """The whole point of the split: only the spelling handed to the engine moves."""
    from doc2video.tools.tts import TTSTool

    written = "这里最关键的是 RAG 模块。"
    result = TTSTool(settings).synthesize(
        "", tmp_path / "out.wav", sentences=[written, "它把检索和生成接在一起。"]
    )
    assert result.segments[0].text == written


def test_a_second_reading_is_named_only_where_the_engine_gets_it_wrong():
    """「日更」 is gēng, and every engine tried reads it as gèng.

    A text-in/audio-out engine gives one lever — write a homophone of the
    reading that is wanted — so the word is spelled with 耕. The list stays
    short on purpose: a wrong entry mispronounces a word that was previously
    right, so a term earns a line only after being heard getting it wrong.
    """
    from doc2video.tools.tts.pronounce import for_speech

    assert for_speech("更新按需，小时更、日更、月更。") == "耕新按需，小时耕、日耕、月耕。"
    assert for_speech("数据每天更新一次。") == "数据每天耕新一次。"

    # 更 as 「more」 is the reading the engine already gets right.
    said = "这个方案更好一些，更多细节见附录。"
    assert for_speech(said) == said


def test_a_stand_in_whose_own_reading_moves_is_no_stand_in():
    """「不」 is the commonest character reading bù, and the wrong one to use.

    It was chosen for 部 in 「部分」, and 「不份」 is *bú*fèn — 不 drops to second
    tone before a fourth-tone syllable. The rule that makes 一 and 不 unsafe to
    replace makes them unsafe to replace *with*.
    """
    from doc2video.tools.tts.polyphone import for_reading

    said = for_reading("供应链的部分需要调整")
    assert "不" not in said, f"变调字不能当替身：{said}"
    assert "布份" in said, f"部分应该写成 bù fèn 的同音字：{said}"


def test_a_rule_the_engine_already_applies_is_left_to_it():
    """Tone sandhi is not a polyphone.

    「一」 is yī, yí or yì depending only on what follows, and every engine
    applies that itself. Writing one instance of the rule into the text breaks
    it everywhere else: 「它是唯一一个」 came back as 「它是唯一宜各」.
    """
    from doc2video.tools.tts.polyphone import for_reading

    assert for_reading("它是唯一一个") == "它是唯一一个"
    assert for_reading("不是一个方案") == "不是一个方案"


def test_only_the_engine_that_needs_the_rewriting_gets_it():
    """写成同音字，是给读不准多音字的引擎用的补丁，不是给所有引擎的。

    `say` reads 「银行行长」 with two identical 行, so the words are rewritten
    into characters that can only be read one way before they are sent — 「行业」
    as 「杭业」 — which sounds right and looks like nonsense, and that is fine
    because nobody reads it.

    A neural voice works the reading out from the sentence, and there the
    rewriting is all cost. Measured across one 30-page film: 84 substitutions,
    of which the useful ones fixed nothing the engine was getting wrong, and
    three were errors of their own — 「与」 as 「欲」 and 「结构」 as 「接构」 are
    both a tone out, and 「目的地」 came back as 「目地第」.

    What survives for every engine is the hand-written list and the letters:
    those were added because a voice was heard getting them wrong.
    """
    from doc2video.tools.tts.base import TTSProvider
    from doc2video.tools.tts.pronounce import for_speech
    from doc2video.tools.tts.providers import MacOSSayProvider

    assert TTSProvider.reads_polyphones is True, "默认假定引擎自己读得准"
    assert MacOSSayProvider.reads_polyphones is False, "say 读不准，要替它改写"

    said = "政策与赛道风险，产业链结构和目的地变化。"
    assert for_speech(said, reading=False) == said, "神经引擎不该被改写"

    rewritten = for_speech(said, reading=True)
    assert rewritten != said, "say 仍然要改写"

    # The two that stay, whichever engine is speaking.
    assert for_speech("AI 日更一次", reading=False) == "诶爱 日耕一次"
