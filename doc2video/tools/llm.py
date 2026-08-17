"""LLM / VLM tool.

Business logic never talks to a vendor SDK directly — skills depend on this
interface only, which is what lets the document/narration/director skills stay
model-agnostic (方案 §6).

Two providers can serve that interface: the Messages API (``AnthropicLLM``,
needs a key) and the locally installed Claude Code CLI (``ClaudeCodeLLM``,
needs no key). With no usable provider the tool reports ``available = False``
and every skill falls back to its deterministic heuristic path, so the whole
pipeline still runs end to end without network access.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
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
    # Which backend answered — reported by `doctor` and /health/capabilities,
    # since "Claude via an API key" and "Claude via the local CLI" have very
    # different cost and latency profiles.
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


NO_LLM_HINT = "请设置 ANTHROPIC_API_KEY，或安装并登录 Claude Code CLI"


class MockLLM(LLMTool):
    """No-op provider. Present so the pipeline has a uniform tool to hold."""

    available = False
    model = "mock"
    source = "mock"

    def complete_json(self, prompt: str, **kwargs) -> dict[str, Any]:  # noqa: ARG002
        raise ToolFailed(f"未配置 LLM，无法进行语义理解；{NO_LLM_HINT}")

    def complete_text(self, prompt: str, **kwargs) -> str:  # noqa: ARG002
        raise ToolFailed(f"未配置 LLM，无法生成文本；{NO_LLM_HINT}")


class AnthropicLLM(LLMTool):
    """Claude-backed implementation.

    Uses streaming plus ``.get_final_message()`` everywhere: outputs here are
    long (whole-deck narration) and non-streaming requests at high max_tokens
    risk HTTP timeouts.
    """

    available = True
    source = "anthropic_api"

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


class ClaudeCodeLLM(LLMTool):
    """Claude Code CLI-backed implementation — the no-API-key path.

    Runs the locally installed ``claude`` binary headlessly
    (``claude -p --output-format json``), so a machine where Claude Code is
    already signed in needs no ``ANTHROPIC_API_KEY``. Skills cannot tell the
    difference; three things do differ, all absorbed here:

    * **No structured outputs.** The schema is written into the prompt and the
      reply is parsed defensively, with one corrective retry — the API's
      ``json_schema`` guarantee does not exist over the CLI.
    * **No image blocks.** Page renders are read off disk by the CLI's own
      ``Read`` tool, the only tool this provider ever allows.
    * **Fixed per-call overhead.** Every invocation carries the CLI's system
      prompt and built-in tool definitions (~20k input tokens, mostly served
      from the prompt cache on consecutive calls). This is the convenient
      path, not the cheap one — prefer an API key for bulk runs.
    """

    available = True
    source = "claude_code"

    # Replaces Claude Code's own coding-agent framing when a skill passes no
    # system prompt of its own.
    DEFAULT_SYSTEM = "你是一个严格按要求输出的内容生成助手。"

    def __init__(self, settings: Settings) -> None:
        self._binary = _resolve_claude_cli(settings)
        if not self._binary:
            raise RuntimeError("未找到 claude 可执行文件（Claude Code CLI 未安装）")
        # A binary on PATH that cannot even print its version is worse than no
        # binary at all: fail here so get_llm() can fall through to the next
        # provider instead of failing once per skill.
        try:
            probe = subprocess.run(
                [self._binary, "--version"], capture_output=True, text=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Claude Code CLI 无法执行：{exc}") from exc
        if probe.returncode != 0:
            raise RuntimeError("Claude Code CLI 无法执行（--version 返回非零）")

        self.model = settings.llm_model
        self._timeout = settings.claude_cli_timeout
        self._version = probe.stdout.strip()

    # -- public API ----------------------------------------------------
    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict,
        system: str = "",
        images: list[Path] | None = None,
        max_tokens: int | None = None,  # noqa: ARG002 - the CLI has no output cap
    ) -> dict[str, Any]:
        paths = [path for path in (images or []) if path.exists()]
        request = _json_request(prompt, schema, paths)
        text = self._invoke(request, system=system, images=paths)
        try:
            return _parse_json_reply(text)
        except ToolFailed:
            log.debug("Claude Code CLI 首次返回的不是合法 JSON，重试一次")
            corrected = f"{request}\n\n上一次回复不是合法 JSON。只输出 JSON 对象本身。"
            return _parse_json_reply(self._invoke(corrected, system=system, images=paths))

    def complete_text(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> str:  # noqa: ARG002
        return self._invoke(prompt, system=system, images=[])

    # -- internals -----------------------------------------------------
    def _command(self, *, system: str, images: list[Path]) -> list[str]:
        # The prompt itself goes over stdin, not argv: a whole deck's narration
        # can run to hundreds of KB, and argv is capped (1MB on macOS) — plus
        # argv is world-readable in `ps`.
        command = [
            self._binary,
            "-p",
            "--output-format", "json",
            "--model", self.model,
            "--system-prompt", system or self.DEFAULT_SYSTEM,
            # Drop the working-directory / git preamble: this is a one-shot
            # generation call, not a session in a repository.
            "--exclude-dynamic-system-prompt-sections",
            # Ignore whatever MCP servers the user has configured. None of them
            # belong in a narration call, and their schemas are billed on every
            # single request.
            "--strict-mcp-config",
            "--no-session-persistence",
            # Renders are the only reason to touch the filesystem at all.
            "--allowedTools", "Read" if images else "",
        ]
        for directory in sorted({str(path.resolve().parent) for path in images}):
            command += ["--add-dir", directory]
        return command

    def _invoke(self, prompt: str, *, system: str, images: list[Path]) -> str:
        try:
            result = subprocess.run(
                self._command(system=system, images=images),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                # A neutral directory: a cwd inside a repository would pull that
                # repository's CLAUDE.md into every narration prompt.
                cwd=_cli_workdir(),
            )
        except FileNotFoundError as exc:
            self.available = False
            raise ToolFailed(
                "Claude Code CLI 已消失，后续步骤将使用启发式降级",
                detail={"reason": str(exc)[:200]},
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolFailed(
                f"Claude Code CLI 超时（{self._timeout}s）",
                detail={"timeout": self._timeout},
            ) from exc

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            # Not a per-request failure: the CLI itself could not run (not
            # logged in, bad flag, crashed). Stop paying for it once per skill.
            self.available = False
            raise ToolFailed(
                "Claude Code CLI 没有返回可解析的输出，后续步骤将使用启发式降级",
                detail={"returncode": result.returncode, "stderr": result.stderr[-400:]},
            ) from exc

        if payload.get("is_error") or payload.get("subtype") != "success":
            raise ToolFailed(
                "Claude Code CLI 调用失败",
                detail={
                    "subtype": payload.get("subtype"),
                    "snippet": str(payload.get("result"))[:400],
                },
            )

        text = str(payload.get("result") or "")
        if not text.strip():
            raise ToolFailed("Claude Code CLI 返回了空结果")
        return text


@lru_cache(maxsize=1)
def _cli_workdir() -> str:
    """An empty scratch directory to run the CLI in.

    Only the project-level CLAUDE.md is avoided this way; a user-level one in
    ``~/.claude`` still applies, which is a reason to prefer the API provider
    when narration wording has to be reproducible across machines.
    """
    directory = Path(tempfile.gettempdir()) / "doc2video-claude-cli"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def _resolve_claude_cli(settings: Settings) -> str | None:
    """Configured path wins over PATH, matching how media binaries resolve."""
    configured = settings.claude_cli_path
    if configured and Path(configured).exists():
        return configured
    return shutil.which("claude")


def _json_request(prompt: str, schema: dict, images: list[Path]) -> str:
    parts = [prompt]
    if images:
        listing = "\n".join(f"- {path.resolve()}" for path in images)
        parts.append(f"先用 Read 工具逐个读取下面这些页面渲染图，再作答：\n{listing}")
    parts.append(
        "只输出一个 JSON 对象，且必须匹配下面的 JSON Schema。"
        "不要使用 markdown 代码围栏，不要输出任何解释文字。\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return "\n\n".join(parts)


_FENCE_RE = re.compile(r"\A```[a-zA-Z]*\s*|\s*```\Z")


def _parse_json_reply(text: str) -> dict[str, Any]:
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
        "Claude Code CLI 返回的结构化结果不是合法 JSON 对象", detail={"snippet": text[:400]}
    )


def _outermost_object(text: str) -> str | None:
    """The ``{...}`` span, for replies that wrap the object in prose."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else None


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


PROVIDERS: dict[str, type[LLMTool]] = {
    "anthropic": AnthropicLLM,
    "claude_code": ClaudeCodeLLM,
}

# What ``auto`` tries, in order: an API key is cheaper and gives real
# structured outputs, so the CLI is the fallback rather than the default.
AUTO_ORDER = ("anthropic", "claude_code")


def get_llm(settings: Settings | None = None) -> LLMTool:
    """Build the configured LLM tool, degrading to MockLLM when unusable."""
    settings = settings or get_settings()
    provider = settings.llm_provider.strip().lower()
    if provider == "mock":
        return MockLLM()

    reasons: list[str] = []
    for name in AUTO_ORDER if provider == "auto" else (provider,):
        factory = PROVIDERS.get(name)
        if factory is None:
            reasons.append(f"{name}：未知的 provider")
            continue
        try:
            return factory(settings)
        except Exception as exc:  # missing key, missing binary, bad config
            reasons.append(f"{name}：{exc}")

    log.warning("LLM 不可用（%s），本次运行使用启发式规则降级处理", "；".join(reasons))
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
