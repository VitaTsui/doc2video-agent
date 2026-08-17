"""Identifier helpers. IDs are readable on purpose — they show up in chat edits."""

from __future__ import annotations

import re
import secrets
import unicodedata


def new_project_id() -> str:
    return f"proj_{secrets.token_hex(6)}"


def new_job_id() -> str:
    return f"job_{secrets.token_hex(6)}"


def scene_id(index: int) -> str:
    """Scene IDs are 1-based and zero-padded so they sort lexicographically."""
    return f"scene_{index:02d}"


def element_id(page_index: int, seq: int, label: str | None = None) -> str:
    """Stable element id, e.g. ``p06_e03_rag``.

    The optional label makes director output readable ("zoom to rag") without
    forcing the LLM to memorise opaque ids.
    """
    base = f"p{page_index:02d}_e{seq:02d}"
    if not label:
        return base
    return f"{base}_{slugify(label)}"


def slugify(text: str, *, max_length: int = 24) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_only).strip("_")
    if not slug:
        # CJK text loses everything above; fall back to a short content hash.
        slug = f"t{abs(hash(text)) % 10000:04d}"
    return slug[:max_length]
