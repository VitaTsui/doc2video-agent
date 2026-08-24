"""What to hand the synthesiser, when it is not what the caption says.

A caption reads 「RAG 模块」 and a narrator says "R-A-G 模块". Both are right,
and they are not the same string — so the text that goes to the engine is not
the text that goes on screen. This module is the small, specific version of
that distinction: the spelling changes, nothing else does, and the subtitle is
never touched.

The list is short on purpose, and every entry was measured rather than
assumed. `say` already spells out most initialisms correctly — AI, PDF, API,
SDK, GPU, KPI, JSON, HTTP, NLP all come back as letters. What it gets wrong is
the ones that happen to be pronounceable English words: RAG comes out as
"rag", MoE as "moe". Measured by duration against the spelled-out form, on
this machine:

    RAG   0.49s   R A G  0.74s   ← read as a word
    LoRA  0.44s   L o R A 0.78s  ← read as a word
    AI    0.52s   A I    0.48s   ← already letters, leave it alone

`SaaS` is read as "sass", which is how people say it — a dictionary that
spelled it out would be making the delivery worse. That is why this is not a
list of "technical terms": it is a list of terms this engine gets wrong.
"""

from __future__ import annotations

import re

# Initialisms that form pronounceable words and are therefore read as words.
# Spelled with spaces, which is what makes a synthesiser name the letters.
# Not here on purpose: LoRA and SaaS are said as words by people too, so the
# engine reading them as words is the engine being right.
SPELL_OUT = {
    "RAG": "R A G",
    "MoE": "M o E",
    "ONNX": "O N N X",
    "NER": "N E R",
    "CoT": "C o T",
    "DAU": "D A U",
    "MAU": "M A U",
}

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+]*")

# An all-caps run this long or shorter is an initialism, and its letters are
# what it is. Measured with `say`: 「CCAI」 comes back in 0.40 seconds — a word,
# not four letters, which take 1.01. Five letters and up is left alone, because
# that is where the caps start being a word in capitals: 「SKILL.md」 is a file
# name, not S-K-I-L-L.
SPELL_LIMIT = 4
# Read as words by people, so reading them as letters would be the mistake.
# Two kinds: acronyms that became words, and ordinary words a slide happens to
# set in capitals — a deck's 「MAIL」 is mail, not M-A-I-L.
SAID_AS_WORDS = frozenset(
    {
        "NASA", "OPEC", "IEEE", "SAAS", "JSON", "YAML", "SQL", "GUI", "JAVA", "DEMO",
        "MAIL", "HOME", "NEXT", "TEAM", "DATA", "TIME", "USER", "PLUS", "MENU", "NEWS",
        "OPEN", "FREE", "MORE", "BEST", "TOP", "NEW", "ALL", "END", "MAP", "WEB", "APP",
    }
)


def _is_initialism(token: str) -> bool:
    """Whether this Latin token is a set of letters rather than a word."""
    return (
        token.isupper()
        and token.isalpha()
        and 2 <= len(token) <= SPELL_LIMIT
        and token not in SAID_AS_WORDS
    )


def for_speech(text: str, extra: dict[str, str] | None = None) -> str:
    """`text` as it should be spoken. The caption keeps the original.

    `extra` is the project's own dictionary, which wins over this one: a deck
    about a company called RAG is talking about the company.
    """
    table = {**SPELL_OUT, **(extra or {})}

    # Latin terms are matched as whole tokens — 「AI」 must not fire inside
    # 「MAIL」. Anything else has no token boundaries to speak of, so it is a
    # plain substring, longest first: a deck that registers both 「中试」 and
    # 「应用中试」 means the longer one.
    latin = {k: v for k, v in table.items() if _TOKEN.fullmatch(k)}
    other = sorted((k for k in table if k not in latin), key=len, reverse=True)

    lookup = {key.upper(): value for key, value in latin.items()}

    def swap(match: re.Match[str]) -> str:
        token = match.group(0)
        if (named := lookup.get(token.upper())) is not None:
            return named
        if _is_initialism(token):
            return " ".join(token)
        return token

    spoken = _TOKEN.sub(swap, text)
    for term in other:
        spoken = spoken.replace(term, table[term])
    # A deck that registers both 「中试」 and 「应用中试」 has the shorter entry
    # fire again inside the longer one's replacement. Two boundaries in a row
    # are one boundary.
    return re.sub(r"  +", " ", spoken)
