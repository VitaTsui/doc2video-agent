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


def for_speech(text: str, extra: dict[str, str] | None = None) -> str:
    """`text` as it should be spoken. The caption keeps the original.

    `extra` is the project's own dictionary, which wins over this one: a deck
    about a company called RAG is talking about the company.
    """
    table = {**SPELL_OUT, **(extra or {})}
    if not table:
        return text

    lookup = {key.upper(): value for key, value in table.items()}

    def swap(match: re.Match[str]) -> str:
        return lookup.get(match.group(0).upper(), match.group(0))

    return _TOKEN.sub(swap, text)
