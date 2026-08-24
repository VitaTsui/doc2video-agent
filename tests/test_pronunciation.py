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
