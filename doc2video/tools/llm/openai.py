"""OpenAI, and everything that speaks its dialect.

One class serves two providers because the wire format is the same: ``openai``
talks to api.openai.com, ``compatible`` talks to whatever ``llm_base_url``
points at — DeepSeek, 通义, 月之暗面, 硅基流动, a self-hosted gateway. They are
kept as separate provider *names* so `doctor`, telemetry and error messages can
say which one answered; "gpt-5 via OpenAI" and "gpt-5 via someone's gateway"
fail in very different ways.

Structured output is where the dialect stops being identical, so it degrades in
three steps rather than assuming: strict ``json_schema`` → ``json_object`` →
plain text parsed by hand. A gateway that supports none of them still works;
the reply just has to survive ``parse_json_reply``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from ...core.telemetry import LLMUsage, record_llm
from .base import (
    LLMTool,
    encode_image,
    existing_images,
    json_instruction,
    media_type,
    parse_json_reply,
)

log = get_logger(__name__)


class OpenAILLM(LLMTool):
    available = True
    source = "openai"
    # api.openai.com wants max_completion_tokens (max_tokens is rejected by the
    # reasoning models); most gateways still only know max_tokens. Whichever we
    # guess wrong is corrected once, at the first request, and remembered.
    _token_param = "max_completion_tokens"

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        key = self._key(settings)
        if not key:
            raise RuntimeError(self._missing_key_hint())
        base_url = settings.llm_base_url.strip() or None
        if self.source == "compatible" and not base_url:
            raise RuntimeError("兼容通道必须填 llm_base_url")

        self._openai = __import__("openai")
        self._client = OpenAI(api_key=key, base_url=base_url)
        self.model = settings.llm_model.strip() or self._default_model()
        self._max_tokens = settings.llm_max_tokens

    # -- provider identity ---------------------------------------------
    def _key(self, settings: Settings) -> str:
        return settings.openai_api_key.strip()

    def _default_model(self) -> str:
        return "gpt-5"

    def _missing_key_hint(self) -> str:
        return "未配置 OPENAI_API_KEY"

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
        budget = max_tokens or self._max_tokens

        for response_format, asking in self._format_ladder(schema):
            messages = self._messages(prompt + asking, system, images)
            try:
                text = self._send(messages, budget, response_format)
            except self._openai.BadRequestError as exc:
                # The gateway does not know this response_format. Try the next
                # rung; only the last one failing is a real error.
                log.debug("response_format 不被接受，降级：%s", str(exc)[:200])
                continue
            return parse_json_reply(text, source=self.source)

        raise ToolFailed(f"{self.source} 无法返回结构化结果", detail={"model": self.model})

    def complete_text(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> str:
        return self._send(self._messages(prompt, system, None), max_tokens, None)

    # -- internals -----------------------------------------------------
    @staticmethod
    def _format_ladder(schema: dict) -> list[tuple[dict | None, str]]:
        """Each rung, and what the prompt has to say for it to work.

        The top rung carries the shape on the wire, so the prompt says nothing.
        The other two have to say it in words, and both reasons are real:

        - ``json_object`` is *rejected* unless the word JSON appears in the
          messages — DeepSeek answers 「Prompt must contain the word 'json' in
          some form」 with a 400, which reads exactly like a gateway that does
          not support the rung, so the ladder stepped past it;
        - with no ``response_format`` at all the model has been told nothing,
          and answers however it likes.

        Together those two took every batch of a 30-page deck to the bottom
        rung, where DeepSeek replied with a Markdown outline — headings, bullet
        points and a table — and 「不是合法 JSON 对象」 five times over.
        """
        asking = json_instruction(schema)
        return [
            (
                {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "schema": schema, "strict": True},
                },
                "",
            ),
            ({"type": "json_object"}, asking),
            (None, asking),
        ]

    def _messages(self, prompt: str, system: str, images: list[Path] | None) -> list[dict]:
        blocks: list[dict] = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type(p)};base64,{encode_image(p)}"},
            }
            for p in existing_images(images)
        ]
        blocks.append({"type": "text", "text": prompt})

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        # A text-only request is sent as a plain string: some gateways reject
        # the block form outright, and there is nothing to gain from it here.
        messages.append({"role": "user", "content": blocks if len(blocks) > 1 else prompt})
        return messages

    def _send(self, messages: list[dict], max_tokens: int, response_format: dict | None) -> str:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if response_format is not None:
            kwargs["response_format"] = response_format

        started = time.monotonic()
        response = self._create(kwargs, max_tokens)
        self._report_usage(response, time.monotonic() - started)

        choice = response.choices[0] if response.choices else None
        text = getattr(getattr(choice, "message", None), "content", None)
        if not text:
            raise ToolFailed("模型响应中没有文本内容", detail={"model": self.model})
        return text

    def _create(self, kwargs: dict[str, Any], max_tokens: int):
        """Send, correcting the token parameter name once if it is rejected."""
        try:
            return self._client.chat.completions.create(
                **kwargs, **{self._token_param: max_tokens}
            )
        except self._openai.BadRequestError as exc:
            other = "max_tokens" if self._token_param != "max_tokens" else "max_completion_tokens"
            if self._token_param not in str(exc):
                raise
            log.debug("%s 不被接受，改用 %s", self._token_param, other)
            type(self)._token_param = other
            return self._client.chat.completions.create(**kwargs, **{other: max_tokens})

    def _report_usage(self, response, duration_s: float) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        record_llm(
            LLMUsage(
                provider=self.source,
                model=getattr(response, "model", self.model),
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                cache_read_tokens=cached,
                duration_s=duration_s,
            )
        )


class CompatibleLLM(OpenAILLM):
    """Any OpenAI-dialect endpoint, addressed by base_url."""

    source = "compatible"
    _token_param = "max_tokens"

    def _key(self, settings: Settings) -> str:
        # Falls back to the OpenAI key so a gateway that reuses it needs one
        # field filled in, not two.
        return (settings.compatible_api_key or settings.openai_api_key).strip()

    def _default_model(self) -> str:
        return ""

    def _missing_key_hint(self) -> str:
        return "未配置兼容通道的 API Key"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if not self.model:
            raise RuntimeError("兼容通道必须指定 llm_model（网关不提供默认模型）")
