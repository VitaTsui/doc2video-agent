"""Gemini, via the google-genai SDK.

Its structured output is not JSON Schema but an OpenAPI 3.0 subset: the
``additionalProperties`` that ``to_strict_schema`` adds for the other two
vendors is rejected here, so it is stripped on the way out. If the schema is
refused anyway the request is retried asking only for ``application/json`` and
the reply is parsed by hand — a slightly loose object beats no narration.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from ...core.telemetry import LLMUsage, record_llm
from .base import LLMTool, existing_images, media_type, parse_json_reply

log = get_logger(__name__)


class GeminiLLM(LLMTool):
    available = True
    source = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai

        key = settings.gemini_api_key.strip()
        if not key:
            raise RuntimeError("未配置 GEMINI_API_KEY")
        self._genai = genai
        self._client = genai.Client(api_key=key)
        self.model = settings.llm_model.strip() or "gemini-2.5-pro"
        self._max_tokens = settings.llm_max_tokens

    # -- public API ----------------------------------------------------
    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict,
        system: str = "",
        images: list[Path] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        contents = self._contents(prompt, images)
        budget = max_tokens or self._max_tokens

        for response_schema in (_openapi_schema(schema), None):
            try:
                text = self._send(contents, system, budget, response_schema)
            except Exception as exc:
                if response_schema is None:
                    raise
                log.debug("Gemini 拒绝了 response_schema，改为只要 JSON：%s", str(exc)[:200])
                continue
            return parse_json_reply(text, source=self.source)

        raise ToolFailed("Gemini 无法返回结构化结果", detail={"model": self.model})

    def complete_text(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> str:
        return self._send(self._contents(prompt, None), system, max_tokens, json=False)

    # -- internals -----------------------------------------------------
    def _contents(self, prompt: str, images: list[Path] | None) -> list:
        from google.genai import types

        parts = [
            types.Part.from_bytes(data=path.read_bytes(), mime_type=media_type(path))
            for path in existing_images(images)
        ]
        parts.append(types.Part.from_text(text=prompt))
        return [types.Content(role="user", parts=parts)]

    def _send(
        self,
        contents: list,
        system: str,
        max_tokens: int,
        schema: dict | None = None,
        *,
        json: bool = True,
    ) -> str:
        from google.genai import types

        config: dict[str, Any] = {"max_output_tokens": max_tokens}
        if system:
            config["system_instruction"] = system
        if json:
            config["response_mime_type"] = "application/json"
            if schema:
                config["response_schema"] = schema

        started = time.monotonic()
        response = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(**config),
        )
        self._report_usage(response, time.monotonic() - started)

        text = getattr(response, "text", None)
        if not text:
            raise ToolFailed("模型响应中没有文本内容", detail={"model": self.model})
        return text

    def _report_usage(self, response, duration_s: float) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        record_llm(
            LLMUsage(
                provider=self.source,
                model=self.model,
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
                duration_s=duration_s,
            )
        )


def _openapi_schema(node: Any) -> Any:
    """Drop the keys Gemini's schema dialect does not accept."""
    if isinstance(node, list):
        return [_openapi_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {k: _openapi_schema(v) for k, v in node.items() if k != "additionalProperties"}
