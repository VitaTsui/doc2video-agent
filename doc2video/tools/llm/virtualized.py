"""A locally installed CLI Agent, used as a model.

Speaks the ``agent-virtualization/model-provider/v1`` NDJSON protocol to the
``agent-virtualization`` package, which wraps Claude Code / Codex and keeps
their own reasoning loop intact while confining what they may *do* to an Action
Space the host declares. See ``docs/model-provider-protocol.md`` in that repo.

Two things about this integration are deliberate:

**The Action Space is empty.** We want a model, not an agent: the prompt already
carries everything — page text, element ids, the character budget — and there is
nothing on this machine the deck's narration should be reading. Declaring no
tools is what makes "use a CLI agent as a model" mean the same thing as "call an
API". A ``tool.call`` should therefore never arrive; if one does it is answered
with a failure rather than ignored, because the bridge suspends the CLI loop
until the host replies and silence would hang the run until it timed out.

**No images.** The protocol carries a task string, not attachments, so
``supports_images`` is False and the understanding step sends text only. A CLI
agent could read a PNG off disk if given the tool, but that means handing it a
filesystem capability to save one round trip.

One bridge process serves exactly one ``model.run`` and then closes, so each
call spawns a process. That is the protocol's design — it is what lets the CLI
keep its context across host tool round trips — and it costs one CLI startup per
batch, which is why the batches are pages-at-a-time rather than page-at-a-time.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ...core import programs
from ...core.config import Settings
from ...core.errors import ToolFailed
from ...core.logging import get_logger
from ...core.telemetry import LLMUsage, record_llm
from .base import LLMTool, parse_json_reply

log = get_logger(__name__)

PROTOCOL = "agent-virtualization/model-provider/v1"
PACKAGE = "agent-virtualization"

# The CLIs this can drive, and the binary each needs on PATH. For this provider
# the choice of "model" *is* the choice of runtime — there is nothing else to
# pick — so `llm_model` names one of these.
RUNTIMES = {"claude-code": "claude", "codex": "codex"}


def runtime_of(settings: Settings) -> str:
    """Which CLI answers. ``llm_model`` wins so the UI has one field per provider."""
    return settings.llm_model.strip() or settings.agent_cli_runtime.strip() or "claude-code"

JSON_INSTRUCTION = (
    "\n\n只输出一个 JSON 对象，不要加解释文字，也不要包在代码块里。"
    "它必须符合这个 JSON Schema：\n"
)

# The bridge streams the CLI's own events; a single line is bounded so a runaway
# agent cannot exhaust memory here (the protocol doc asks hosts to do this).
MAX_LINE_BYTES = 8 * 1024 * 1024

# How much of the CLI's stderr, and how many bridge events, to keep for a turn
# that failed. Neither carries the CLI's own message — it goes to the CLI's own
# output, which the runtime consumes — but together they say how far it got.
STDERR_LINES = 40
EVENT_LINES = 12


class VirtualizedCLILLM(LLMTool):
    """Claude Code / Codex on this machine, addressed as a model."""

    available = True
    source = "agent_virtualization"

    def __init__(self, settings: Settings) -> None:
        self._command = _resolve_command(settings)
        self._node_dir = settings.node_dir
        self._config = _resolve_config(settings)
        self._workspace = settings.storage_dir / "agent-workspace"
        self._timeout = settings.agent_cli_timeout
        self.model = _runtime_name(self._config)
        self._stderr: list[str] = []
        self._events: list[str] = []

    # -- public API ----------------------------------------------------
    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict,
        system: str = "",
        images: list[Path] | None = None,  # noqa: ARG002 - see module docstring
        max_tokens: int | None = None,  # noqa: ARG002 - the CLI budgets itself
    ) -> dict[str, Any]:
        task = prompt + JSON_INSTRUCTION + json.dumps(schema, ensure_ascii=False)
        return parse_json_reply(self._run(task, system), source=PACKAGE)

    def complete_text(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> str:  # noqa: ARG002
        return self._run(prompt, system)

    def supports_images(self) -> bool:
        return False

    # -- the bridge ----------------------------------------------------
    def _run(self, task: str, system: str) -> str:
        self._workspace.mkdir(parents=True, exist_ok=True)
        request_id = f"d2v-{uuid.uuid4().hex[:8]}"
        request: dict[str, Any] = {
            "protocol": PROTOCOL,
            "type": "model.run",
            "requestId": request_id,
            "task": task,
            "workspace": str(self._workspace.resolve()),
            "tools": [],
            "metadata": {"provider": PACKAGE, "model": self.model},
        }
        if system:
            request["instructions"] = system

        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 - argv is built here, never shell
            [*self._command, "model", "--config", str(self._config)],
            # Run from the Node workspace: that is where the package is
            # installed, and `npx --no-install` resolves against the cwd.
            cwd=self._node_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._stderr, self._events = [], []
        if process.stderr is not None:
            # Drained on a thread: a full pipe would block the CLI itself, and
            # this is where the reason for a failed turn is written.
            threading.Thread(target=self._collect, args=(process.stderr,), daemon=True).start()

        try:
            output = self._exchange(process, request, request_id)
        finally:
            # Closing stdin cancels any pending wait and disposes the run; kill
            # the tree if it does not go quietly.
            _terminate(process)

        record_llm(
            LLMUsage(
                provider=self.source,
                model=self.model,
                duration_s=time.monotonic() - started,
            )
        )
        return output

    def _collect(self, stream) -> None:
        for line in stream:
            if len(self._stderr) == STDERR_LINES:
                self._stderr.pop(0)
            self._stderr.append(line.rstrip())

    def _output_of(self, result: dict) -> str:
        """The CLI's answer, or the best explanation available for why there is none.

        The bridge reports a failed turn as a status and a short message; the
        reason the user can act on — hitting a plan's usage limit, being logged
        out — is written by the CLI to stderr. Reporting only the status turns
        every one of those into an unhelpful "failed".
        """
        status = result.get("status")
        output = (result.get("output") or "").strip()
        if status != "completed":
            raise ToolFailed(
                f"{self.model} 未正常结束（{status}）。"
                f"它自己的报错不会经过这里——直接跑一次 `{RUNTIMES[self.model]}` 通常"
                f"就能看到原因（没登录、额度用尽、网络不通）。",
                detail={
                    "error": str(result.get("error") or result.get("message") or "")[:300],
                    "events": self._events[-4:],
                    "stderr": self.diagnostics()[-400:],
                },
            )
        if not output:
            raise ToolFailed("CLI Agent 没有输出内容")
        return output

    def diagnostics(self) -> str:
        """Recent stderr from the bridge and the CLI under it."""
        return "\n".join(self._stderr)

    def _exchange(self, process: subprocess.Popen, request: dict, request_id: str) -> str:
        assert process.stdin is not None and process.stdout is not None
        _send(process.stdin, request)

        deadline = time.monotonic() + self._timeout
        for line in process.stdout:
            if time.monotonic() > deadline:
                raise ToolFailed(f"{PACKAGE} 超时（{self._timeout}s）")
            if len(line) > MAX_LINE_BYTES or not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # the bridge promises NDJSON on stdout; ignore noise
            if message.get("protocol") != PROTOCOL:
                continue

            kind = message.get("type")
            if kind == "model.result":
                return self._output_of(message.get("result") or {})
            if kind == "model.error":
                # The bridge's own words, not just "it failed": its errors are
                # about paths and credentials, and a caller that logs only the
                # summary leaves someone guessing at an ENOENT it could have
                # simply shown them.
                reason = str(message.get("error"))[:300]
                raise ToolFailed(f"{PACKAGE} 报错：{reason}", detail={"error": reason})
            if kind == "tool.call":
                # Should not happen with an empty Action Space, but the CLI loop
                # is suspended until we answer — never leave it hanging.
                _send(
                    process.stdin,
                    {
                        "protocol": PROTOCOL,
                        "type": "tool.result",
                        "requestId": request_id,
                        "callId": message.get("callId"),
                        "success": False,
                        "content": "本次调用没有开放任何工具，请直接给出答案。",
                    },
                )
            elif kind == "model.event":
                event = str(message.get("event"))[:300]
                log.debug("agent 事件：%s", event)
                if len(self._events) == EVENT_LINES:
                    self._events.pop(0)
                self._events.append(event)

        raise ToolFailed(f"{PACKAGE} 未返回结果", detail={"stderr": self.diagnostics()[-600:]})


def _send(stream, message: dict) -> None:
    stream.write(json.dumps(message, ensure_ascii=False) + "\n")
    stream.flush()





def _terminate(process: subprocess.Popen) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _resolve_command(settings: Settings) -> list[str]:
    """How to start the bridge, preferring an install over a download.

    An explicit path wins; then the package's own bin on PATH; then ``npx``
    against the Node workspace, where it is a declared dependency and therefore
    already on disk. Bare ``npx agent-virtualization`` with no local copy is not
    used — it would reach for the network mid-render.
    """
    configured = settings.agent_cli_path.strip()
    if configured:
        if not Path(configured).exists():
            raise RuntimeError(f"agent_cli_path 指向的文件不存在：{configured}")
        return [configured]

    found = shutil.which(PACKAGE)
    if found:
        return [found]

    node_dir = settings.node_dir
    # The resolved path, not the bare name. On Windows `npx` is `npx.cmd`, and
    # `CreateProcess` will not run a shim by its bare name — it answers
    # `[WinError 2] 系统找不到指定的文件`, for a command `shutil.which` had just
    # found. Using the check's own answer is the whole fix, and this is the
    # third place in this repo to have needed it.
    npx = programs.find("npx")
    if npx and (node_dir / "node_modules" / PACKAGE).exists():
        return [npx, "--no-install", PACKAGE]

    raise RuntimeError(f"未安装 {PACKAGE}（在 Node 工作区 npm install，或设置 D2V_AGENT_CLI_PATH）")


def _resolve_config(settings: Settings) -> Path:
    """The bridge's config file, generating a default when none was given.

    Hand-writing one is a bad first experience and easy to get subtly wrong —
    omitting ``homeMode: inherit`` produces "Not logged in · Please run /login"
    from a CLI the user is, in fact, logged into, because the spawned process
    never saw their home directory.
    """
    # Absolute throughout: the bridge is spawned with the renderer as its
    # working directory (that is where its node_modules live), so a relative
    # path — and `storage_dir` defaults to the relative `./storage` — resolves
    # against the wrong directory and comes back as ENOENT from a process that
    # cannot say which path it meant.
    configured = settings.agent_cli_config.strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"agent-virtualization 配置文件不存在：{path}")
        return path

    runtime = runtime_of(settings)
    if runtime not in RUNTIMES:
        raise RuntimeError(f"不认识的 CLI 运行时：{runtime}（可选 {'、'.join(RUNTIMES)}）")
    binary = RUNTIMES[runtime]
    if shutil.which(binary) is None:
        raise RuntimeError(f"未安装 {binary}（{runtime}）")

    # One file per runtime: switching between them must not silently reuse the
    # other's config, which differs in how credentials are inherited.
    path = (settings.storage_dir / f"agent-virtualization.{runtime}.json").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_default_config(runtime, settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# What the sandboxed CLI needs from our environment, and nothing else.
#
# The sandbox scrubs the environment by default, which is right — but it also
# strips the proxy, and on a machine that can only reach the model's API
# through one, the CLI then goes direct and is refused:
#
#     Failed to authenticate. API Error: 403 Request not allowed
#
# It reads as a credential problem and is a routing one. Named explicitly
# rather than inherited wholesale: everything else in that environment is ours
# and none of it is the CLI's business.
PROXY_VARS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "no_proxy",
)


def _default_config(runtime: str, settings: Settings) -> dict[str, Any]:
    """No tools, no network restriction, nothing writable that matters.

    The CLI is being used as a model, so every capability it might be given is
    one it does not need. The workspace still has to exist and be writable —
    the runtimes put their own scratch state there — but nothing we care about
    lives in it.

    Credential inheritance is the one thing the two runtimes disagree about,
    and getting it wrong looks identical from the outside: a CLI the user is
    logged into reports itself logged out. claude-code inherits by seeing the
    real HOME; codex takes an explicit runtime flag.
    """
    workspace = (settings.storage_dir / "agent-workspace").resolve()
    return {
        "runtime": (
            {"type": runtime}
            if runtime != "codex"
            else {"type": "codex", "inheritHostCredentials": True}
        ),
        "environment": {
            "instructions": "直接给出答案，不要使用任何工具。",
            "capabilities": [],
            "policy": {"defaultDecision": "deny", "escalation": "deny", "rules": []},
            "workspace": {"root": str(workspace), "writableRoots": [str(workspace)]},
            # Network stays open: the CLI Agent *is* the model, and denying it
            # means denying it its own backend. The filesystem is what is
            # confined here, and the workspace holds nothing but its scratch.
            "sandbox": {"mode": "workspace-write", "network": "inherit"},
            # Network is inherited above; the way *to* the network has to be
            # passed as well, or "inherit" means a route this machine does not
            # have.
            "inheritEnv": [name for name in PROXY_VARS if os.environ.get(name)],
            # claude-code's half of the same problem: without the real HOME it
            # cannot see the credentials it was logged in with.
            **({"homeMode": "inherit"} if runtime == "claude-code" else {}),
            "timeoutMs": settings.agent_cli_timeout * 1000,
        },
    }


def _runtime_name(config: Path) -> str:
    """Label the run with the CLI behind it — "claude-code" is not "codex"."""
    try:
        loaded = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PACKAGE
    runtime = loaded.get("runtime") or {}
    return str(runtime.get("type") or PACKAGE)
