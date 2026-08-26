"""Choosing a model, and surviving every way that can go wrong.

The registry's job is not to make a model work — it is to make *not* having one
a normal outcome. Every test here is offline: a provider that cannot be built
must degrade to MockLLM, and MockLLM must be something the pipeline can hold.
"""

from __future__ import annotations

import base64
import json
import os
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
elif mode == "image":
    emit({"protocol": PROTOCOL, "type": "tool.call", "requestId": rid,
          "callId": "c1", "name": "view_image", "arguments": {"name": "page_001.png"}})
    reply = json.loads(sys.stdin.readline())
    block = (reply.get("blocks") or [{}])[0]
    emit({"protocol": PROTOCOL, "type": "model.result", "requestId": rid,
          "result": {"status": "completed", "output": json.dumps({
              "declared": [tool["name"] for tool in request["tools"]],
              "offered": request["task"].count("page_001.png"),
              "type": block.get("type"),
              "mime": block.get("mimeType"),
              "bytes": len(block.get("data") or ""),
              "text": reply.get("content"),
          })}})
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
    assert tool.supports_images() is True
    # Events on the way past are ignored, and prose around the object survives.
    assert tool.complete_json("讲讲这一页", schema={"type": "object"}) == {"task": "讲讲这一页"}


@pytest.mark.skipif(sys.platform == "win32", reason="用 sh 启动的桩进程")
def test_a_tool_request_is_refused_instead_of_hanging(tmp_path: Path):
    """The CLI loop is suspended until the host replies; silence would deadlock.

    A turn with no images declares no tools at all, so this call is outside the
    Action Space however it got made.
    """
    tool = VirtualizedCLILLM(_bridge_settings(tmp_path, "tool"))
    assert tool.complete_json("写点东西", schema={"type": "object"}) == {"answered": True}


@pytest.mark.skipif(sys.platform == "win32", reason="用 sh 启动的桩进程")
def test_an_attached_image_reaches_the_cli_as_image_content(tmp_path: Path):
    """The picture itself, not a path into a filesystem the CLI cannot see.

    A one-pixel PNG, so what is asserted is the shape of the round trip: the
    tool is declared only because an image came with the turn, the task names
    what can be asked for, and the answer carries the bytes as an image block
    while `content` stays text.
    """
    png = tmp_path / "page_001.png"
    png.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    ))
    tool = VirtualizedCLILLM(_bridge_settings(tmp_path, "image"))

    seen = tool.complete_json("这一页没有文字", schema={"type": "object"}, images=[png])

    assert seen["declared"] == ["view_image"]
    assert seen["offered"] == 1  # the task says which names can be asked for
    assert seen["type"] == "image"
    assert seen["mime"] == "image/png"
    assert seen["bytes"] > 0
    # Base64 rides in the block alone: `content` is the text projection, and is
    # what the bridge writes to its audit record.
    assert seen["text"] == "page_001.png"


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


# -- which CLI answers ----------------------------------------------------
def test_the_model_field_chooses_the_cli(tmp_path: Path):
    """For this provider there is nothing else to pick, so the model *is* the CLI."""
    from doc2video.tools.llm.virtualized import runtime_of

    base = {"storage_dir": tmp_path}
    assert runtime_of(Settings(**base)) == "claude-code"
    assert runtime_of(Settings(**base, llm_model="codex")) == "codex"
    # The env-only knob still works, and the UI's field wins over it.
    assert runtime_of(Settings(**base, agent_cli_runtime="codex")) == "codex"
    assert runtime_of(Settings(**base, agent_cli_runtime="codex", llm_model="claude-code")) == (
        "claude-code"
    )


def test_each_runtime_gets_the_credential_option_it_understands(tmp_path: Path):
    """The two disagree here, and getting it wrong looks identical from outside:
    a CLI the user is logged into reports itself logged out."""
    from doc2video.tools.llm.virtualized import _default_config

    settings = Settings(storage_dir=tmp_path)
    binary = Path("/usr/local/bin/claude")
    claude = _default_config("claude-code", settings, binary)
    codex = _default_config("codex", settings, binary)

    assert claude["environment"]["homeMode"] == "inherit"
    assert "inheritHostCredentials" not in claude["runtime"]

    assert codex["runtime"]["inheritHostCredentials"] is True
    assert "homeMode" not in codex["environment"]

    # Neither is given a single tool: we want a model, not an agent.
    for config in (claude, codex):
        assert config["environment"]["capabilities"] == []
        assert config["environment"]["policy"]["defaultDecision"] == "deny"


def test_an_unknown_runtime_is_refused_by_name(tmp_path: Path):
    settings = Settings(storage_dir=tmp_path, llm_provider="agent_cli", llm_model="emacs")
    assert isinstance(get_llm(settings), MockLLM)


def test_the_bridge_is_told_where_its_config_is_in_absolute_terms(tmp_path, monkeypatch):
    """A relative path here resolves against the wrong directory.

    The bridge runs with the renderer as its working directory — that is where
    its node_modules are — while `storage_dir` defaults to a relative
    `./storage`. Handing it `storage/x.json` made it open
    `renderer/storage/x.json` and fail with an ENOENT naming a path nobody
    configured. Downstream that surfaced as an agent with no model: the deck
    was understood by heuristics and the loop answered "我没想清楚下一步".
    """
    import shutil as shutil_module

    from doc2video.tools.llm import virtualized

    monkeypatch.setattr(virtualized.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.chdir(tmp_path)
    assert shutil_module.which  # the real one is untouched outside the module

    path = virtualized._resolve_config(Settings(storage_dir=Path("storage")))
    assert path.is_absolute(), path
    assert path.parent == (tmp_path / "storage").resolve()


def test_the_bridges_own_words_survive_the_exception(tmp_path):
    """"报错" alone is not a diagnosis.

    Its failures are about paths and credentials — the useful half is the
    sentence it wrote. Keeping that only in a `detail` dict meant the log line
    someone actually reads said nothing at all.
    """
    import json as json_module
    import subprocess

    from doc2video.core.errors import ToolFailed
    from doc2video.tools.llm.virtualized import PROTOCOL, VirtualizedCLILLM

    llm = VirtualizedCLILLM.__new__(VirtualizedCLILLM)
    llm._timeout = 5
    reply = json_module.dumps(
        {"protocol": PROTOCOL, "type": "model.error", "error": "ENOENT: /nowhere/x.json"}
    )
    process = subprocess.Popen(
        ["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        with pytest.raises(ToolFailed) as caught:
            llm._exchange(process, json_module.loads(reply), "r1", {})
    finally:
        process.kill()

    assert "ENOENT: /nowhere/x.json" in str(caught.value)


def test_a_quotation_mark_a_model_forgot_to_escape_does_not_lose_the_reply():
    """The failure this repairs is ordinary, not exotic.

    A slide titled 《（五）三类"战略决策"场景总览》 comes back with the page's own
    quotation marks copied straight into a JSON string. One such page threw
    away a whole six-page batch of understanding on every run of a real deck,
    and the only trace was a degradation reading "不是合法 JSON 对象".
    """
    from doc2video.tools.llm.base import parse_json_reply

    reply = '{"title":"（五）三类"战略决策"场景总览","index":23}'
    assert parse_json_reply(reply) == {"title": '（五）三类"战略决策"场景总览', "index": 23}


def test_the_repair_never_runs_on_a_reply_that_already_parses():
    """It is the last rung, so it cannot rewrite something already correct."""
    from doc2video.tools.llm.base import parse_json_reply

    proper = '{"a": "he said \\"hi\\"", "b": [1, 2], "c": {"d": null}}'
    assert parse_json_reply(proper) == {"a": 'he said "hi"', "b": [1, 2], "c": {"d": None}}


def test_a_reply_that_is_simply_not_json_still_fails():
    """Repairing must not turn "the model refused" into a silent empty answer."""
    import pytest as _pytest

    from doc2video.core.errors import ToolFailed
    from doc2video.tools.llm.base import parse_json_reply

    with _pytest.raises(ToolFailed):
        parse_json_reply("我不能回答这个问题。")


def test_the_catalogue_says_which_local_cli_is_actually_installed(monkeypatch):
    """Offering a CLI that is not there is offering a failure.

    Every other provider's availability depends on a key the app cannot see,
    so the list stays a list. The local ones are knowable, and a machine with
    only one of them should be told so here rather than at the first request —
    which is where it surfaced as "模型没有给出可用的决定".
    """
    from doc2video.tools.llm import models as catalogue_module

    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/claude" if name == "claude" else None
    )
    rows = {row["id"]: row for row in catalogue_module.catalogue_payload()["agent_cli"]}

    assert rows["claude-code"]["installed"] is True
    assert "/usr/local/bin/claude" in rows["claude-code"]["note"]
    assert rows["codex"]["installed"] is False
    assert "未检测到" in rows["codex"]["note"]

    # Nothing else grows the field: their availability is not knowable here.
    assert "installed" not in catalogue_module.catalogue_payload()["anthropic"][0]


def test_the_sandboxed_cli_is_given_the_way_out_that_the_machine_uses(monkeypatch):
    """`network: inherit` is not enough when the route is a proxy.

    The sandbox scrubs the environment, which is right — but it also strips
    the proxy, and on a machine that can only reach the model's API through
    one the CLI then goes direct and is refused with
    `403 Request not allowed`. That reads as a credential problem and is a
    routing one.
    """
    from doc2video.tools.llm.virtualized import _default_config

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    binary = Path("/usr/local/bin/claude")
    environment = _default_config("claude-code", Settings(), binary)["environment"]

    assert environment["sandbox"]["network"] == "inherit"
    assert environment["inheritEnv"] == ["HTTPS_PROXY"]

    # Only what is set. Naming an absent variable would hand the sandbox an
    # empty proxy setting, which some clients read as "proxy to nowhere".
    monkeypatch.delenv("HTTPS_PROXY")
    assert _default_config("claude-code", Settings(), binary)["environment"]["inheritEnv"] == []


def test_the_sandboxed_cli_can_be_executed_and_knows_who_it_is(monkeypatch):
    """Two ways a logged-in CLI reports itself unusable, both environmental.

    The sandbox starts from a scrubbed environment and then runs the CLI by
    name, so a CLI installed outside the system directories cannot be spawned
    at all — `execvp() of 'claude' failed`, for a program the check right
    above just located. And on macOS the credentials are in the login
    keychain, which the CLI finds by user: without `USER` it says
    「Not logged in · Please run /login」 on a machine where the same binary
    answers fine from a shell. Both end the same way — the pipeline degrades
    to placeholder narration and nobody learns why.
    """
    from doc2video.tools.llm.virtualized import _default_config

    monkeypatch.setenv("USER", "someone")
    monkeypatch.delenv("LOGNAME", raising=False)
    environment = _default_config(
        "claude-code", Settings(), Path("/opt/homebrew/bin/claude")
    )["environment"]

    assert "USER" in environment["inheritEnv"]
    assert environment["env"]["PATH"].split(os.pathsep)[0] == "/opt/homebrew/bin"


def test_lower_rungs_ask_for_json_in_words():
    """`json_object` 是被「拒绝」的，不是「不支持」——差别在提示词里有没有 json。

    DeepSeek 对不含这个词的请求回 400：「Prompt must contain the word 'json'
    in some form」。那个 400 长得和「网关不认识这一档」一模一样，阶梯于是一路
    踩到最底下——而最底下既没格式约束也没在提示里要 JSON，模型回了一篇
    Markdown 大纲。一份 30 页文档的五个批次全是这么降级的。
    """
    from doc2video.tools.llm.openai import OpenAILLM

    ladder = OpenAILLM._format_ladder({"type": "object", "properties": {}})
    formats = [fmt for fmt, _ in ladder]
    assert formats[0]["type"] == "json_schema"
    assert formats[1]["type"] == "json_object"
    assert formats[2] is None

    # 顶档把形状写在协议里，提示词不用重复说。
    assert ladder[0][1] == ""
    # 下面两档必须自己说，而且必须出现 json 这个词。
    for _, asking in ladder[1:]:
        assert "json" in asking.lower(), asking
        assert "JSON Schema" in asking
