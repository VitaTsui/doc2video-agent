"""What the engine is told to say, when that differs from what is written.

A caption reads 「RAG 模块」 and a narrator says "R-A-G 模块". Both are right,
and the strings are not the same — so the text handed to the synthesiser is
not the text put on screen. Getting this backwards is audible: one wrong term
makes a whole video sound cheap.
"""

from __future__ import annotations

from doc2video.agent.planner import parse_intent_rules
from doc2video.schemas import VideoIntent
from doc2video.tools.tts.pronounce import SPELL_OUT, for_speech


def test_an_initialism_read_as_a_word_is_spelled_out():
    """Measured, not assumed: `say` reads RAG as "rag", 0.49s against 0.74s."""
    assert for_speech("这里最关键的是 RAG 模块。") == "这里最关键的是 R A G 模块。"


def test_the_terms_the_engine_already_gets_right_are_left_alone():
    """The list is of terms this engine gets wrong, not of technical terms.

    `say` spells out most initialisms correctly on its own — AI, PDF, API,
    SDK, GPU, KPI, JSON, HTTP all come back as letters. Adding them would be
    churn; and SaaS is read as "sass", which is how people say it, so a
    dictionary that spelled it out would make the delivery worse.
    """
    text = "SaaS 产品的 API、SDK 和 JSON 接口，AI 与 GPU 都在里面。"
    assert for_speech(text) == text
    assert "SaaS" not in SPELL_OUT
    assert "AI" not in SPELL_OUT


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
