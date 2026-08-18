"""A curated model list, for the desktop settings panel to render.

Deliberately not exhaustive and never authoritative: model ids change faster
than a shipped app updates, so this is a convenience list and any id can be
typed in by hand. Nothing in the pipeline reads it — ``llm_model`` is passed to
the provider verbatim.

``vision`` matters to more than the settings dialog: the understanding step
attaches page renders, and sending images to a text-only model is a wasted
request rather than a graceful one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    vision: bool = True
    note: str = ""


CATALOGUE: dict[str, list[ModelInfo]] = {
    # For the local CLI provider the "model" is which CLI answers — there is
    # nothing else to choose, and each one brings its own model.
    "agent_cli": [
        ModelInfo("claude-code", "Claude Code", vision=False, note="需要已登录的 claude"),
        ModelInfo("codex", "Codex", vision=False, note="需要已登录的 codex"),
    ],
    "anthropic": [
        ModelInfo("claude-opus-5", "Claude Opus 5", note="最强，讲稿质量最好"),
        ModelInfo("claude-sonnet-5", "Claude Sonnet 5", note="快且便宜，日常够用"),
        ModelInfo("claude-haiku-4-5", "Claude Haiku 4.5", note="最快，长稿会偏平"),
    ],
    "openai": [
        ModelInfo("gpt-5", "GPT-5"),
        ModelInfo("gpt-5-mini", "GPT-5 mini", note="便宜"),
    ],
    "gemini": [
        ModelInfo("gemini-2.5-pro", "Gemini 2.5 Pro"),
        ModelInfo("gemini-2.5-flash", "Gemini 2.5 Flash", note="便宜"),
    ],
    # The compatible channel has no default list: whatever the gateway serves.
    "compatible": [
        ModelInfo("deepseek-chat", "DeepSeek Chat", vision=False),
        ModelInfo("qwen-plus", "通义千问 Plus"),
        ModelInfo("moonshot-v1-32k", "月之暗面 32K", vision=False),
    ],
}


def catalogue_payload() -> dict[str, list[dict]]:
    """JSON-ready copy for the API."""
    return {provider: [asdict(m) for m in models] for provider, models in CATALOGUE.items()}


def is_vision_model(provider: str, model_id: str) -> bool:
    """Whether attaching page renders is worth the bytes.

    Unknown ids are assumed to see — the catalogue is a convenience, not a
    gate, and refusing to send images to a model we simply have not listed
    would silently degrade the better setup.
    """
    for info in CATALOGUE.get(provider, []):
        if info.id == model_id:
            return info.vision
    return True
