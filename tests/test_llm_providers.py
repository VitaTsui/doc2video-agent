"""Choosing a model, and surviving every way that can go wrong.

The registry's job is not to make a model work — it is to make *not* having one
a normal outcome. Every test here is offline: a provider that cannot be built
must degrade to MockLLM, and MockLLM must be something the pipeline can hold.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from doc2video.core.config import Settings
from doc2video.core.errors import ToolFailed
from doc2video.tools.llm import MockLLM, get_llm, llm_status
from doc2video.tools.llm.base import parse_json_reply, to_strict_schema
from doc2video.tools.llm.virtualized import VirtualizedCLILLM


# -- registry ------------------------------------------------------------
def test_the_default_holds_no_model(tmp_path: Path):
    """The service's contract is unchanged: the caller writes the script."""
    assert isinstance(get_llm(Settings(storage_dir=tmp_path)), MockLLM)


def test_a_provider_without_a_key_degrades_rather_than_raising(tmp_path: Path):
    tool = get_llm(Settings(storage_dir=tmp_path, llm_provider="anthropic", anthropic_api_key=""))
    assert isinstance(tool, MockLLM)


def test_an_unknown_provider_degrades(tmp_path: Path):
    assert isinstance(get_llm(Settings(storage_dir=tmp_path, llm_provider="pigeon")), MockLLM)


def test_the_compatible_channel_needs_a_base_url(tmp_path: Path):
    """A gateway with no address is not a gateway."""
    tool = get_llm(
        Settings(storage_dir=tmp_path, llm_provider="compatible", compatible_api_key="k")
    )
    assert isinstance(tool, MockLLM)


def test_mock_refuses_loudly_so_try_llm_can_catch_it(tmp_path: Path):
    tool = get_llm(Settings(storage_dir=tmp_path))
    with pytest.raises(ToolFailed):
        tool.complete_text("你好")
    assert tool.supports_images() is False


def test_status_names_the_configured_provider_even_when_it_failed(tmp_path: Path):
    status = llm_status(Settings(storage_dir=tmp_path, llm_provider="openai"))
    assert status["configured"] == "openai"
    assert status["available"] is False


# -- schema and reply handling -------------------------------------------
def test_strict_schema_closes_objects_and_drops_unsupported_keys():
    strict = to_strict_schema(
        {
            "type": "object",
            "properties": {"n": {"type": "integer", "minimum": 1, "default": 2}},
        }
    )
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["n"]
    assert "minimum" not in strict["properties"]["n"]
    assert "default" not in strict["properties"]["n"]


def test_a_reply_wrapped_in_prose_or_a_fence_still_parses():
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('好的，结果是：{"a": 1}，希望有帮助') == {"a": 1}
    with pytest.raises(ToolFailed):
        parse_json_reply("完全不是 JSON")


# -- the CLI-agent bridge ------------------------------------------------
BRIDGE = '''
import json, sys
PROTOCOL = "agent-virtualization/model-provider/v1"
mode = sys.argv[-1]
request = json.loads(sys.stdin.readline())
rid = request["requestId"]

def emit(message):
    sys.stdout.write(json.dumps(message) + "\\n")
    sys.stdout.flush()

if mode == "error":
    emit({"protocol": PROTOCOL, "type": "model.error", "requestId": rid, "error": "boom"})
elif mode == "failed":
    emit({"protocol": PROTOCOL, "type": "model.result", "requestId": rid,
          "result": {"status": "timed_out", "output": "", "error": "too slow"}})
elif mode == "tool":
    emit({"protocol": PROTOCOL, "type": "tool.call", "requestId": rid,
          "callId": "c1", "name": "write_file", "arguments": {}})
    reply = json.loads(sys.stdin.readline())
    emit({"protocol": PROTOCOL, "type": "model.result", "requestId": rid,
          "result": {"status": "completed",
                     "output": json.dumps({"answered": reply["success"] is False})}})
else:
    emit({"protocol": PROTOCOL, "type": "model.event", "requestId": rid, "event": {"t": "noise"}})
    emit({"protocol": PROTOCOL, "type": "model.result", "requestId": rid,
          "result": {"status": "completed",
                     "output": "前言\\n{\\"task\\": %s}" % json.dumps(request["task"][:5])}})
'''


def _bridge_settings(tmp_path: Path, mode: str) -> Settings:
    """A stand-in for the bridge process, so these tests need no Node."""
    script = tmp_path / "bridge.py"
    script.write_text(BRIDGE, encoding="utf-8")
    launcher = tmp_path / ("run.sh" if sys.platform != "win32" else "run.bat")
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@" {mode}\n')
    launcher.chmod(0o755)

    config = tmp_path / "agent.json"
    config.write_text(json.dumps({"runtime": {"type": "claude-code"}}), encoding="utf-8")
    return Settings(
        storage_dir=tmp_path / "store",
        llm_provider="agent_cli",
        agent_cli_path=str(launcher),
        agent_cli_config=str(config),
        agent_cli_timeout=30,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="用 sh 启动的桩进程")
def test_the_bridge_answer_is_parsed_and_labelled(tmp_path: Path):
    tool = VirtualizedCLILLM(_bridge_settings(tmp_path, "ok"))

    assert tool.model == "claude-code"  # the runtime behind it, not "the CLI"
    assert tool.supports_images() is False
    # Events on the way past are ignored, and prose around the object survives.
    assert tool.complete_json("讲讲这一页", schema={"type": "object"}) == {"task": "讲讲这一页"}


@pytest.mark.skipif(sys.platform == "win32", reason="用 sh 启动的桩进程")
def test_a_tool_request_is_refused_instead_of_hanging(tmp_path: Path):
    """The CLI loop is suspended until the host replies; silence would deadlock."""
    tool = VirtualizedCLILLM(_bridge_settings(tmp_path, "tool"))
    assert tool.complete_json("写点东西", schema={"type": "object"}) == {"answered": True}


@pytest.mark.skipif(sys.platform == "win32", reason="用 sh 启动的桩进程")
def test_a_bridge_error_becomes_a_tool_failure(tmp_path: Path):
    tool = VirtualizedCLILLM(_bridge_settings(tmp_path, "error"))
    with pytest.raises(ToolFailed):
        tool.complete_text("你好")


@pytest.mark.skipif(sys.platform == "win32", reason="用 sh 启动的桩进程")
def test_an_unfinished_run_is_not_reported_as_an_answer(tmp_path: Path):
    """A timed-out CLI returns a result message with an empty output."""
    tool = VirtualizedCLILLM(_bridge_settings(tmp_path, "failed"))
    with pytest.raises(ToolFailed):
        tool.complete_text("你好")


@pytest.mark.skipif(sys.platform == "win32", reason="用 sh 启动的桩进程")
def test_a_broken_bridge_degrades_the_whole_registry(tmp_path: Path):
    settings = _bridge_settings(tmp_path, "ok")
    settings.agent_cli_path = str(tmp_path / "nope")
    assert isinstance(get_llm(settings), MockLLM)
