"""The interface skills depend on, plus what every provider needs.

Business logic never talks to a vendor SDK directly — the document and
narration skills depend on this interface only, which is what lets them stay
model-agnostic (方案 §6). A provider that cannot be built (no key, SDK not
installed, bad base_url) is not an error: ``get_llm`` returns ``MockLLM`` and
every skill takes its deterministic heuristic path, so the pipeline still runs
end to end with no network at all.

The JSON helpers live here rather than in one provider because the providers
split into two kinds: those with native structured output (Anthropic, OpenAI,
Gemini) that need a *strict* schema, and everything reachable through an
OpenAI-compatible gateway, where "JSON mode" is a suggestion and the reply can
still arrive wrapped in prose or a code fence.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from ...core.errors import ToolFailed
from ...core.logging import get_logger

log = get_logger(__name__)

NO_LLM_HINT = "在设置里配置一个模型 API Key，或让调用方自己写讲稿"


class LLMTool:
    """Base interface. ``available`` tells skills whether to attempt a call."""

    available: bool = False
    model: str = ""
    # Which backend answered — reported by `doctor` and /health/capabilities.
    # "gpt-5 via OpenAI" and "gpt-5 via someone's gateway" fail differently.
    source: str = "mock"

    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict,
        system: str = "",
        images: list[Path] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def complete_text(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> str:
        raise NotImplementedError

    def supports_images(self) -> bool:
        """Whether page renders are worth attaching. Text-only models say no."""
        return True


class MockLLM(LLMTool):
    """No-op provider. Present so the pipeline has a uniform tool to hold."""

    available = False
    model = "mock"
    source = "mock"

    def complete_json(self, prompt: str, **kwargs) -> dict[str, Any]:  # noqa: ARG002
        raise ToolFailed(f"未配置模型，无法进行语义理解；{NO_LLM_HINT}")

    def complete_text(self, prompt: str, **kwargs) -> str:  # noqa: ARG002
        raise ToolFailed(f"未配置模型，无法生成文本；{NO_LLM_HINT}")

    def supports_images(self) -> bool:
        return False


# --------------------------------------------------------------------------
# JSON Schema helpers for structured outputs
# --------------------------------------------------------------------------

# Structured outputs reject numeric/length constraints; strip them rather than
# letting a Pydantic-generated schema fail validation at request time.
_UNSUPPORTED_KEYS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "default",
    "format",
}


def to_strict_schema(schema: dict) -> dict:
    """Normalize a JSON Schema for the structured-outputs APIs.

    Every object gets ``additionalProperties: false`` and lists all its
    properties as required — the APIs' requirement, and also what keeps the
    model from inventing extra keys the parser would silently ignore.
    """
    return _strictify(json.loads(json.dumps(schema)))


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned = {k: _strictify(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYS}

    if cleaned.get("type") == "object" or "properties" in cleaned:
        properties = cleaned.get("properties", {})
        cleaned["additionalProperties"] = False
        cleaned["required"] = list(properties.keys())
    return cleaned


def model_schema(model_cls) -> dict:
    """Strict JSON Schema for a Pydantic model, ready to send as output format."""
    return to_strict_schema(model_cls.model_json_schema())


_FENCE_RE = re.compile(r"\A```[a-zA-Z]*\s*|\s*```\Z")


def parse_json_reply(text: str, *, source: str = "模型") -> dict[str, Any]:
    """Recover a JSON object from a reply that has no format guarantee."""
    candidate = _FENCE_RE.sub("", text.strip())
    for attempt in (candidate, _outermost_object(candidate)):
        if attempt is None:
            continue
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ToolFailed(
        f"{source}返回的结构化结果不是合法 JSON 对象", detail={"snippet": text[:400]}
    )


def _outermost_object(text: str) -> str | None:
    """The ``{...}`` span, for replies that wrap the object in prose."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else None


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------


def media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/png")


def encode_image(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("ascii")


def existing_images(images: list[Path] | None) -> list[Path]:
    """Drop paths that are not on disk — a missing page render is not fatal."""
    return [p for p in images or [] if p.exists()]
