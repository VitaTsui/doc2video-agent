"""LLM / VLM tool.

Business logic never talks to a vendor SDK directly — skills depend on this
interface only, which is what lets the document/narration/director skills stay
model-agnostic (方案 §6).

When no API key is configured the tool reports ``available = False`` and every
skill falls back to its deterministic heuristic path, so the whole pipeline
still runs end to end without network access.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from ..core.config import Settings, get_settings
from ..core.errors import ToolFailed
from ..core.logging import get_logger

log = get_logger(__name__)

# Server-side refusal fallback: on a policy decline the API re-runs the request
# on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMTool:
    """Base interface. ``available`` tells skills whether to attempt a call."""

    available: bool = False
    model: str = ""

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


class MockLLM(LLMTool):
    """No-op provider. Present so the pipeline has a uniform tool to hold."""

    available = False
    model = "mock"

    def complete_json(self, prompt: str, **kwargs) -> dict[str, Any]:  # noqa: ARG002
        raise ToolFailed("未配置 LLM，无法进行语义理解；请设置 ANTHROPIC_API_KEY")

    def complete_text(self, prompt: str, **kwargs) -> str:  # noqa: ARG002
        raise ToolFailed("未配置 LLM，无法生成文本；请设置 ANTHROPIC_API_KEY")


class AnthropicLLM(LLMTool):
    """Claude-backed implementation.

    Uses streaming plus ``.get_final_message()`` everywhere: outputs here are
    long (whole-deck narration) and non-streaming requests at high max_tokens
    risk HTTP timeouts.
    """

    available = True

    def __init__(self, settings: Settings) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        if not _has_credentials(self._client):
            raise RuntimeError("未找到 Claude 凭据（ANTHROPIC_API_KEY / ant auth login）")
        self.model = settings.llm_model
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
        content = self._build_content(prompt, images)
        message = self._send(
            content=content,
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
        blocks: list[dict] = []
        for image_path in images or []:
            if not image_path.exists():
                continue
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": _media_type(image_path),
                        "data": base64.standard_b64encode(image_path.read_bytes()).decode("ascii"),
                    },
                }
            )
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

    @staticmethod
    def _first_text(message) -> str:
        for block in message.content:
            if block.type == "text":
                return block.text
        raise ToolFailed("模型响应中没有文本内容")


def _has_credentials(client) -> bool:
    """Check up front whether a credential exists at all.

    The SDK resolves an API key, an auth token, or an ``ant auth login`` profile
    on disk. Detecting "nothing configured" here avoids burning one failed
    request per skill before falling back.
    """
    import os

    if getattr(client, "api_key", None) or getattr(client, "auth_token", None):
        return True
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    config_dir = Path(os.environ.get("ANTHROPIC_CONFIG_DIR", "~/.config/anthropic")).expanduser()
    return (config_dir / "credentials").is_dir()


def _media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/png")


def get_llm(settings: Settings | None = None) -> LLMTool:
    """Build the configured LLM tool, degrading to MockLLM when unusable."""
    settings = settings or get_settings()
    if settings.llm_provider == "mock":
        return MockLLM()
    try:
        return AnthropicLLM(settings)
    except Exception as exc:  # missing key, missing package, bad config
        log.warning("LLM 不可用（%s），本次运行使用启发式规则降级处理", exc)
        return MockLLM()


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
    """Normalize a JSON Schema for the structured-outputs API.

    Every object gets ``additionalProperties: false`` and lists all its
    properties as required — the API's requirement, and also what keeps the
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
