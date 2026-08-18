"""Claude, via the Anthropic Messages API.

Streaming plus ``.get_final_message()`` everywhere: outputs here are long — a
whole deck's narration in one call — and a non-streaming request at high
``max_tokens`` risks an HTTP timeout rather than a useful error.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from ...core.telemetry import LLMUsage, record_llm
from .base import LLMTool, encode_image, existing_images, media_type

log = get_logger(__name__)

# Server-side refusal fallback: on a policy decline the API re-runs the request
# on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM(LLMTool):
    available = True
    source = "anthropic"

    def __init__(self, settings: Settings) -> None:
        import anthropic

        key = settings.anthropic_api_key.strip()
        if not key:
            raise RuntimeError("未配置 ANTHROPIC_API_KEY")
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=key, base_url=settings.llm_base_url.strip() or None
        )
        self.model = settings.llm_model.strip() or "claude-opus-5"
        self._effort = settings.llm_effort
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
        message = self._send(
            content=self._build_content(prompt, images),
            system=system,
            max_tokens=max_tokens or self._max_tokens,
            output_format={"type": "json_schema", "schema": schema},
        )
        text = self._first_text(message)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolFailed(
                "模型返回的结构化结果不是合法 JSON", detail={"snippet": text[:400]}
            ) from exc

    def complete_text(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> str:
        message = self._send(
            content=[{"type": "text", "text": prompt}],
            system=system,
            max_tokens=max_tokens,
            output_format=None,
        )
        return self._first_text(message)

    # -- internals -----------------------------------------------------
    def _build_content(self, prompt: str, images: list[Path] | None) -> list[dict]:
        blocks: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type(path),
                    "data": encode_image(path),
                },
            }
            for path in existing_images(images)
        ]
        blocks.append({"type": "text", "text": prompt})
        return blocks

    def _send(
        self,
        *,
        content: list[dict],
        system: str,
        max_tokens: int,
        output_format: dict | None,
    ):
        output_config: dict[str, Any] = {"effort": self._effort}
        if output_format is not None:
            output_config["format"] = output_format

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "output_config": output_config,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = system

        started = time.monotonic()
        try:
            message = self._stream_with_fallbacks(kwargs)
        except (TypeError, self._anthropic.AuthenticationError) as exc:
            # No usable credential. Mark the tool unavailable so the rest of the
            # run goes straight to the heuristic path instead of retrying per skill.
            self.available = False
            raise ToolFailed(
                "Claude 凭据不可用，后续步骤将使用启发式降级",
                detail={"reason": str(exc)[:200]},
            ) from exc

        self._report_usage(message, time.monotonic() - started)

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise ToolFailed(
                "模型拒绝了本次请求",
                detail={"category": getattr(details, "category", None)},
            )
        return message

    def _stream_with_fallbacks(self, kwargs: dict[str, Any]):
        """Prefer the server-side refusal fallback; degrade if unsupported.

        The beta only affects what happens on a policy decline, so an SDK or
        deployment without it must not break ordinary requests.
        """
        try:
            with self._client.beta.messages.stream(
                **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
            ) as stream:
                return stream.get_final_message()
        except TypeError as exc:
            # Only swallow "this SDK does not know `fallbacks`" — an auth-related
            # TypeError must surface so the caller can degrade properly.
            if "fallback" not in str(exc).lower():
                raise
            log.debug("SDK 不支持 fallbacks 参数，降级为普通请求")
        except self._anthropic.BadRequestError as exc:
            log.debug("服务端 fallback 不可用，降级为普通请求：%s", exc)
        with self._client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    def _report_usage(self, message, duration_s: float) -> None:
        """Hand token counts to telemetry.

        A refused message is reported too — it was billed, and a run that spent
        tokens without producing anything is exactly what monitoring should see.
        """
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        record_llm(
            LLMUsage(
                provider=self.source,
                model=getattr(message, "model", self.model),
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                duration_s=duration_s,
            )
        )

    @staticmethod
    def _first_text(message) -> str:
        for block in message.content:
            if block.type == "text":
                return block.text
        raise ToolFailed("模型响应中没有文本内容")
