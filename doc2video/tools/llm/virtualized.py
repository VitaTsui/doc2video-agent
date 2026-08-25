"""A locally installed CLI Agent, used as a model.

Speaks the ``agent-virtualization/model-provider/v1`` NDJSON protocol to the
``agent-virtualization`` package, which wraps Claude Code / Codex and keeps
their own reasoning loop intact while confining what they may *do* to an Action
Space the host declares. See ``docs/model-provider-protocol.md`` in that repo.

Two things about this integration are deliberate:

**The Action Space holds one tool, and only when there is an image.** We want a
model, not an agent: the prompt already carries everything — page text, element
ids, the character budget — and there is nothing on this machine the deck's
narration should be reading. A text-only turn therefore declares no tools at
all, which is what makes "use a CLI agent as a model" mean the same thing as
"call an API"; a ``tool.call`` arriving in that turn is answered with a failure
rather than ignored, because the bridge suspends the CLI loop until the host
replies and silence would hang the run until it timed out.

**Images travel in band.** The sandbox hides our filesystem from the CLI, so a
page render cannot be handed over as a path — it would name a file the CLI
cannot open. A turn that carries images declares ``view_image`` instead, and
answers the call with the bytes themselves: ``tool.result.blocks`` projects onto
native MCP image content (agent-virtualization >= 0.1.3). The alternative was to
grant a filesystem capability and pass paths; this hands over the one image that
was asked for rather than the disk it sits on.

One bridge process serves exactly one ``model.run`` and then closes, so each
call spawns a process. That is the protocol's design — it is what lets the CLI
keep its context across host tool round trips — and it costs one CLI startup per
batch, which is why the batches are pages-at-a-time rather than page-at-a-time.
"""

from __future__ import annotations

import base64
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

# The only tool this provider ever declares. Named for what it does to the CLI,
# not for what it does to us: it does not read a file, it shows a picture.
VIEW_IMAGE_TOOL = "view_image"

# What the bridge will carry. Anything else is refused at its process boundary,
# so it is refused here first — as one answered tool call rather than as a
# protocol error that takes the whole batch down with it.
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# The bridge's own per-image ceiling, mirrored. Ours are page renders at 1920px
# and land well under it; a deck that somehow renders larger should lose one
# image, not the turn.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

IMAGE_NOTE = (
    "\n\n本次随任务附了这些图：{names}。"
    f"用 {VIEW_IMAGE_TOOL} 工具按名字取，图会直接作为图像交给你。"
    "凡是文字不足以判断的地方——图表、示意图、只有配图没有文字的页——先看图再回答，"
    "不要凭标题猜。"
)


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
        images: list[Path] | None = None,
        max_tokens: int | None = None,  # noqa: ARG002 - the CLI budgets itself
    ) -> dict[str, Any]:
        attached = _attachments(images)
        # The note goes before the schema, not after: the last thing the task
        # says should still be "answer with JSON".
        task = prompt + _image_note(attached) + JSON_INSTRUCTION + json.dumps(schema, ensure_ascii=False)
        return parse_json_reply(self._run(task, system, attached), source=PACKAGE)

    def complete_text(self, prompt: str, *, system: str = "", max_tokens: int = 2000) -> str:  # noqa: ARG002
        return self._run(prompt, system, {})

    def supports_images(self) -> bool:
        return True

    # -- the bridge ----------------------------------------------------
    def _run(self, task: str, system: str, attached: dict[str, Path]) -> str:
        self._workspace.mkdir(parents=True, exist_ok=True)
        request_id = f"d2v-{uuid.uuid4().hex[:8]}"
        request: dict[str, Any] = {
            "protocol": PROTOCOL,
            "type": "model.run",
            "requestId": request_id,
            "task": task,
            "workspace": str(self._workspace.resolve()),
            "tools": _tools_for(attached),
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
            output = self._exchange(process, request, request_id, attached)
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

    def _exchange(
        self,
        process: subprocess.Popen,
        request: dict,
        request_id: str,
        attached: dict[str, Path],
    ) -> str:
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
                # The CLI loop is suspended until we answer — every branch of
                # `_tool_result` returns something, and none of them may raise.
                _send(process.stdin, _tool_result(request_id, message, attached))
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


def _attachments(images: list[Path] | None) -> dict[str, Path]:
    """The images this turn can show, keyed by the name the CLI will ask for.

    Their own file names: a page render is ``page_007.png``, which is what the
    prompt calls that page anyway, so the model does not have to be taught a
    second naming scheme. A name already taken keeps the first path — the second
    file would be unaddressable either way, and dropping it silently beats
    answering with the wrong picture.
    """
    attached: dict[str, Path] = {}
    for path in images or []:
        if path.suffix.lower() not in IMAGE_MEDIA_TYPES:
            log.debug("%s 不是能随协议传的图片格式，跳过", path.name)
            continue
        if path.name not in attached and path.exists():
            attached[path.name] = path
    return attached


def _image_note(attached: dict[str, Path]) -> str:
    return IMAGE_NOTE.format(names="、".join(attached)) if attached else ""


def _tools_for(attached: dict[str, Path]) -> list[dict[str, Any]]:
    """The turn's Action Space: one tool if there is anything to show, else none.

    The attached names are the schema's enum, so a name we never attached is
    refused by the bridge's own argument validation and never reaches us.
    """
    if not attached:
        return []
    return [
        {
            "name": VIEW_IMAGE_TOOL,
            "description": "看一眼随本次任务附上的某张图，返回图本身。",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": sorted(attached),
                        "description": "图片名，取自任务里列出的那几个。",
                    },
                },
                "required": ["name"],
            },
        }
    ]


def _tool_result(request_id: str, message: dict, attached: dict[str, Path]) -> dict[str, Any]:
    """Answer one suspended tool call — with the image, or with why there isn't one.

    Every path returns a message and none of them raises: the CLI's loop stays
    suspended until this reply is written, so a failure has to be a failed call
    it can read and work around, never an exception that leaves it waiting for
    the timeout.
    """
    reply: dict[str, Any] = {
        "protocol": PROTOCOL,
        "type": "tool.result",
        "requestId": request_id,
        "callId": message.get("callId"),
    }
    if message.get("name") != VIEW_IMAGE_TOOL:
        return {**reply, "success": False, "content": "本次调用没有开放这个工具，请直接给出答案。"}

    arguments = message.get("arguments")
    asked = arguments.get("name") if isinstance(arguments, dict) else None
    path = attached.get(asked) if isinstance(asked, str) else None
    if path is None:
        return {
            **reply,
            "success": False,
            "content": f"没有这张图。本次附上的是：{'、'.join(attached)}",
        }

    try:
        payload = path.read_bytes()
    except OSError as exc:
        return {**reply, "success": False, "content": f"读不到 {asked}：{exc}"}
    if len(payload) > MAX_IMAGE_BYTES:
        # Refused here rather than sent: the bridge rejects an oversized result
        # with a protocol error, which would end the whole turn instead of this
        # one look at one image.
        return {
            **reply,
            "success": False,
            "content": f"{asked} 有 {len(payload) // 1024} KiB，超过了单张图的上限，看不了。",
        }

    log.debug("给 CLI 看了 %s（%d KiB）", asked, len(payload) // 1024)
    return {
        **reply,
        "success": True,
        # The text projection of this result. The bytes ride in `blocks`, and
        # keeping them out of here is what keeps base64 out of the audit log.
        "content": asked,
        "blocks": [
            {
                "type": "image",
                "mimeType": IMAGE_MEDIA_TYPES[path.suffix.lower()],
                "data": base64.b64encode(payload).decode("ascii"),
            }
        ],
    }


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
    # The package's own entry point, run by the Node we ship. Not `npx`: the
    # desktop runtime carries Node and the workspace's `node_modules` but not
    # npm's `lib/`, so its `npx` shim dies with MODULE_NOT_FOUND — present,
    # executable, found by `which`, and unable to start anything.
    if command := programs.node_command(node_dir, PACKAGE):
        return command

    # Left as a last resort for a source checkout, where npm is whole.
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
    found = programs.find(binary)
    if found is None:
        raise RuntimeError(f"未安装 {binary}（{runtime}）")

    # One file per runtime: switching between them must not silently reuse the
    # other's config, which differs in how credentials are inherited.
    path = (settings.storage_dir / f"agent-virtualization.{runtime}.json").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_default_config(runtime, settings, Path(found)), ensure_ascii=False, indent=2),
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

# Who is running this. On macOS the CLI's credentials live in the login
# keychain, and it finds that keychain by user — without `USER` it reports
#
#     Not logged in · Please run /login
#
# on a machine where `claude -p` answers perfectly from a shell. Same shape as
# the proxy problem above: a routing failure wearing a credential failure's
# words, and the pipeline then degrades to placeholder narration without
# anyone learning why. `HOME` alone is not enough; this was bisected.
IDENTITY_VARS = ("USER", "LOGNAME")


def _default_config(runtime: str, settings: Settings, binary: Path) -> dict[str, Any]:
    """No capabilities, no network restriction, nothing writable that matters.

    The CLI is being used as a model, so every capability it might be given is
    one it does not need. The workspace still has to exist and be writable —
    the runtimes put their own scratch state there — but nothing we care about
    lives in it. The one tool a turn may declare is not configured here: in
    model-provider mode the request's ``tools`` replace the visible Action
    Space outright, so it is composed per turn and lives no longer than that.

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
            # "no tools at all" was the instruction until a turn could carry
            # images: it now has exactly one, and telling the CLI to ignore its
            # own Action Space would talk it out of looking at them.
            "instructions": "直接给出答案。除了本次开放给你的工具，不要尝试任何其他动作。",
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
            "inheritEnv": [
                name for name in PROXY_VARS + IDENTITY_VARS if os.environ.get(name)
            ],
            # The sandbox starts with a scrubbed environment, PATH included, and
            # then runs the CLI by name — so a CLI installed anywhere but the
            # system directories cannot be executed at all:
            #
            #     sandbox-exec: execvp() of 'claude' failed: No such file or directory
            #
            # We already know where it is; the check above found it. This is the
            # same mistake `core.programs` exists to prevent, one layer further
            # out: look the program up, then spawn a name and hope. Its own
            # directory is enough — these CLIs ship as native binaries, not as
            # scripts needing an interpreter on the path.
            "env": {"PATH": os.pathsep.join([str(binary.parent), os.defpath])},
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
