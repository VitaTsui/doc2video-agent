"""The no-API-key provider: Claude Code CLI standing in for the Messages API.

Nothing here launches the real binary. What is pinned down is the contract the
CLI path has to honour so skills cannot tell which provider answered: a JSON
object comes back even without structured outputs, renders reach the model,
and a broken CLI degrades the run instead of failing it once per skill.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from doc2video.core.config import Settings
from doc2video.core.errors import ToolFailed
from doc2video.tools import llm as llm_module
from doc2video.tools.llm import ClaudeCodeLLM, MockLLM, get_llm

SCHEMA = {"type": "object", "properties": {"topic": {"type": "string"}}}


class FakeCLI:
    """Stands in for ``subprocess.run``, recording every command it is given.

    A reply is either the model's own text (wrapped in a success envelope like
    the CLI does), a full envelope dict, or a raw CompletedProcess.
    """

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.commands: list[list[str]] = []
        self.prompts: list[str] = []

    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.0 (Claude Code)", "")
        self.commands.append(command)
        self.prompts.append(kwargs.get("input", ""))
        reply = self.replies.pop(0)
        if isinstance(reply, subprocess.CompletedProcess):
            return reply
        envelope = reply if isinstance(reply, dict) else _envelope(reply)
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    @property
    def calls(self) -> list[list[str]]:
        """Commands excluding the startup version probe."""
        return self.commands


def _envelope(result: object) -> dict:
    return {"is_error": False, "subtype": "success", "result": result}


def _provider(monkeypatch: pytest.MonkeyPatch, *replies: object) -> tuple[ClaudeCodeLLM, FakeCLI]:
    fake = FakeCLI(*replies)
    monkeypatch.setattr(llm_module.shutil, "which", lambda _name: "/usr/local/bin/claude")
    monkeypatch.setattr(llm_module.subprocess, "run", fake)
    return ClaudeCodeLLM(Settings(llm_provider="claude_code")), fake


def _flag(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


# -- reply parsing ---------------------------------------------------------


def test_fenced_json_is_accepted(monkeypatch: pytest.MonkeyPatch):
    """The CLI has no json_schema mode, so it fences its output like a chat reply."""
    provider, _ = _provider(monkeypatch, '```json\n{"topic": "架构"}\n```')

    assert provider.complete_json("讲讲这份文档", schema=SCHEMA) == {"topic": "架构"}


def test_json_wrapped_in_prose_is_recovered(monkeypatch: pytest.MonkeyPatch):
    provider, _ = _provider(monkeypatch, '好的，结果如下：\n{"topic": "架构"}\n希望有帮助。')

    assert provider.complete_json("讲讲这份文档", schema=SCHEMA) == {"topic": "架构"}


def test_unparsable_reply_is_retried_once(monkeypatch: pytest.MonkeyPatch):
    provider, fake = _provider(monkeypatch, "我需要更多信息才能回答。", '{"topic": "架构"}')

    assert provider.complete_json("讲讲这份文档", schema=SCHEMA) == {"topic": "架构"}
    assert len(fake.calls) == 2
    assert "不是合法 JSON" in fake.prompts[1]


def test_two_bad_replies_fail_the_call(monkeypatch: pytest.MonkeyPatch):
    provider, fake = _provider(monkeypatch, "抱歉。", "还是抱歉。")

    with pytest.raises(ToolFailed):
        provider.complete_json("讲讲这份文档", schema=SCHEMA)
    assert len(fake.calls) == 2
    # A bad answer is not a broken CLI: the next skill still gets to try.
    assert provider.available


def test_a_json_array_is_not_a_valid_object(monkeypatch: pytest.MonkeyPatch):
    """Skills index the reply by key; a list would blow up further downstream."""
    provider, _ = _provider(monkeypatch, "[1, 2, 3]", "[4, 5]")

    with pytest.raises(ToolFailed):
        provider.complete_json("讲讲这份文档", schema=SCHEMA)


# -- what gets sent --------------------------------------------------------


def test_schema_travels_in_the_prompt(monkeypatch: pytest.MonkeyPatch):
    provider, fake = _provider(monkeypatch, '{"topic": "架构"}')

    provider.complete_json("讲讲这份文档", schema=SCHEMA, system="你是导演")

    prompt = fake.prompts[0]
    assert "讲讲这份文档" in prompt
    assert '"topic"' in prompt
    assert _flag(fake.calls[0], "--system-prompt") == "你是导演"


def test_prompt_is_piped_not_passed_as_an_argument(monkeypatch: pytest.MonkeyPatch):
    """A whole deck's narration would otherwise risk the argv size limit."""
    provider, fake = _provider(monkeypatch, '{"topic": "架构"}')

    provider.complete_json("讲讲这份文档", schema=SCHEMA)

    assert "讲讲这份文档" in fake.prompts[0]
    assert not any("讲讲这份文档" in argument for argument in fake.calls[0])


def test_no_images_means_no_tools(monkeypatch: pytest.MonkeyPatch):
    provider, fake = _provider(monkeypatch, '{"topic": "架构"}')

    provider.complete_json("讲讲这份文档", schema=SCHEMA)

    assert _flag(fake.calls[0], "--allowedTools") == ""
    assert "--add-dir" not in fake.calls[0]


def test_renders_are_read_from_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The CLI takes no image blocks, so the paths go in and Read fetches them."""
    render = tmp_path / "assets" / "p1.png"
    render.parent.mkdir()
    render.write_bytes(b"\x89PNG\r\n\x1a\n")
    provider, fake = _provider(monkeypatch, '{"topic": "架构"}')

    provider.complete_json("讲讲这份文档", schema=SCHEMA, images=[render])

    command = fake.calls[0]
    assert _flag(command, "--allowedTools") == "Read"
    assert _flag(command, "--add-dir") == str(render.parent.resolve())
    assert str(render.resolve()) in fake.prompts[0]


def test_missing_render_is_not_announced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A page can name a render that was never produced (see test_vlm)."""
    provider, fake = _provider(monkeypatch, '{"topic": "架构"}')

    provider.complete_json("讲讲这份文档", schema=SCHEMA, images=[tmp_path / "nope.png"])

    assert _flag(fake.calls[0], "--allowedTools") == ""
    assert "Read 工具" not in fake.prompts[0]


def test_user_mcp_servers_are_kept_out(monkeypatch: pytest.MonkeyPatch):
    """Their tool schemas would be billed on every narration call."""
    provider, fake = _provider(monkeypatch, '{"topic": "架构"}')

    provider.complete_json("讲讲这份文档", schema=SCHEMA)

    assert "--strict-mcp-config" in fake.calls[0]
    assert "--exclude-dynamic-system-prompt-sections" in fake.calls[0]


def test_plain_text_completion_returns_the_reply(monkeypatch: pytest.MonkeyPatch):
    provider, _ = _provider(monkeypatch, "一段讲稿。")

    assert provider.complete_text("写一段讲稿") == "一段讲稿。"


# -- failure handling ------------------------------------------------------


def test_unusable_cli_disables_the_provider(monkeypatch: pytest.MonkeyPatch):
    """Not logged in, bad flag, crashed — stop retrying it once per skill."""
    broken = subprocess.CompletedProcess(["claude"], 1, "", "Invalid API key")
    provider, _ = _provider(monkeypatch, broken)

    with pytest.raises(ToolFailed):
        provider.complete_text("写一段讲稿")
    assert not provider.available


def test_error_envelope_fails_only_this_call(monkeypatch: pytest.MonkeyPatch):
    envelope = {"is_error": True, "subtype": "error_max_turns", "result": "…"}
    provider, _ = _provider(monkeypatch, envelope)

    with pytest.raises(ToolFailed):
        provider.complete_text("写一段讲稿")
    assert provider.available


def test_timeout_is_reported_as_a_tool_failure(monkeypatch: pytest.MonkeyPatch):
    def timeout(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "2.1.0", "")
        raise subprocess.TimeoutExpired(command, 600)

    monkeypatch.setattr(llm_module.shutil, "which", lambda _name: "/usr/local/bin/claude")
    monkeypatch.setattr(llm_module.subprocess, "run", timeout)
    provider = ClaudeCodeLLM(Settings(llm_provider="claude_code"))

    with pytest.raises(ToolFailed, match="超时"):
        provider.complete_text("写一段讲稿")


# -- provider selection ----------------------------------------------------


def test_missing_binary_degrades_to_heuristics(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_module.shutil, "which", lambda _name: None)

    tool = get_llm(Settings(llm_provider="claude_code"))

    assert isinstance(tool, MockLLM)
    assert not tool.available


def test_auto_falls_through_to_the_cli(monkeypatch: pytest.MonkeyPatch):
    """No API key on this machine, but Claude Code is installed."""
    fake = FakeCLI()
    monkeypatch.setattr(llm_module.shutil, "which", lambda _name: "/usr/local/bin/claude")
    monkeypatch.setattr(llm_module.subprocess, "run", fake)
    monkeypatch.setattr(
        llm_module, "AnthropicLLM", _raising("未找到 Claude 凭据"), raising=True
    )
    monkeypatch.setitem(llm_module.PROVIDERS, "anthropic", llm_module.AnthropicLLM)

    tool = get_llm(Settings(llm_provider="auto"))

    assert isinstance(tool, ClaudeCodeLLM)
    assert tool.source == "claude_code"


def test_unknown_provider_degrades_instead_of_crashing(monkeypatch: pytest.MonkeyPatch):
    assert isinstance(get_llm(Settings(llm_provider="gpt-9")), MockLLM)


def _raising(message: str):
    class Unavailable:
        def __init__(self, _settings):
            raise RuntimeError(message)

    return Unavailable
