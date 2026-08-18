"""Provider registry — the one place that decides which model answers.

Shaped after the two registries already in the tree (``tts.providers`` and
``renderer.select_adapter``): a name→class map, an ``auto`` order, availability
decided by *constructing* the provider rather than by guessing, and a
deterministic fallback when nothing works.

Building the provider is the availability check. A key can be present but
wrong, a base_url present but unreachable; the constructor is where that shows
up, and a provider that raises is skipped with its reason recorded. The last
resort is ``MockLLM``, which every skill already knows how to survive.
"""

from __future__ import annotations

from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from .base import (
    LLMTool,
    MockLLM,
    model_schema,
    parse_json_reply,
    to_strict_schema,
)
from .models import CATALOGUE, ModelInfo

log = get_logger(__name__)

__all__ = [
    "CATALOGUE",
    "LLMTool",
    "MockLLM",
    "ModelInfo",
    "PROVIDERS",
    "get_llm",
    "model_schema",
    "parse_json_reply",
    "to_strict_schema",
]


def _providers() -> dict[str, type[LLMTool]]:
    """Import providers lazily — an SDK that is not installed is not an error.

    The vendor SDKs are an optional extra, so importing all three at module
    scope would make ``import doc2video`` fail on a lean install. Each is
    imported only when its name is actually asked for.
    """
    from .anthropic import AnthropicLLM
    from .gemini import GeminiLLM
    from .openai import CompatibleLLM, OpenAILLM
    from .virtualized import VirtualizedCLILLM

    return {
        "anthropic": AnthropicLLM,
        "openai": OpenAILLM,
        "gemini": GeminiLLM,
        "compatible": CompatibleLLM,
        "agent_cli": VirtualizedCLILLM,
    }


PROVIDERS = ("anthropic", "openai", "gemini", "compatible", "agent_cli")

# What ``auto`` tries, in order. Not a quality ranking: the local CLI Agent goes
# first because a machine that has one has already paid for it, and using it
# costs the user nothing extra. The keyed providers follow in the order a
# configured key is most likely to be the one that was meant.
AUTO_ORDER = ("agent_cli", "anthropic", "openai", "gemini", "compatible")


def get_llm(settings: Settings | None = None, *, rollout_key: str = "") -> LLMTool:  # noqa: ARG001
    """Build the configured LLM tool, degrading to MockLLM when unusable.

    ``rollout_key`` is accepted so this matches ``select_adapter``'s signature
    and can gate providers by project later; it is unused today.
    """
    settings = settings or get_settings()
    provider = settings.llm_provider.strip().lower()
    if provider in ("", "mock", "none"):
        return MockLLM()

    try:
        registry = _providers()
    except ImportError as exc:
        log.warning("模型 SDK 未安装（%s），本次运行使用启发式规则降级处理", exc)
        return MockLLM()

    reasons: list[str] = []
    for name in AUTO_ORDER if provider == "auto" else (provider,):
        factory = registry.get(name)
        if factory is None:
            reasons.append(f"{name}：未知的 provider")
            continue
        try:
            return factory(settings)
        except Exception as exc:  # missing key, SDK absent, bad base_url
            reasons.append(f"{name}：{exc}")

    log.warning("模型不可用（%s），本次运行使用启发式规则降级处理", "；".join(reasons))
    return MockLLM()


def llm_status(settings: Settings | None = None) -> dict:
    """What `doctor` and /health/capabilities report about this layer."""
    settings = settings or get_settings()
    tool = get_llm(settings)
    return {
        "provider": tool.source,
        "model": tool.model,
        "available": tool.available,
        "configured": settings.llm_provider,
    }
