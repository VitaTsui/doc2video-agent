"""MCP tools over Streamable HTTP.

The same agent, addressed by a model instead of by a person. It shares the API
process's agent and job manager, so an MCP-created project is the same project
`GET /projects/{id}` returns — there is one store, not two.

Three shapes matter here, each forced by something real:

* **The script is the caller's to write.** This service holds no model, so
  ``prepare_project`` hands back the deck's contents *and* the per-page duration
  budget, and ``render_video`` takes the text back. Tool descriptions say so
  plainly, because a model that assumes the server will write it produces a
  video of placeholder sentences.
* **The caller's filesystem is not this machine's.** A local path means nothing
  to a remote server, so a source file arrives as an ``upload_id``.
* **Nothing blocks.** A full render takes minutes; a tool call that waits that
  long ties up the conversation and risks a client timeout. Every tool that
  starts work returns a ``job_id`` immediately.

The module is deliberately named ``mcp_server`` rather than ``mcp``: a package
named ``mcp`` here would be a confusing neighbour to the SDK of the same name.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .agent import JobRequest
from .core import version as core_version
from .core.config import Settings, get_settings
from .core.errors import Doc2VideoError
from .core.logging import get_logger

log = get_logger(__name__)

INSTRUCTIONS = """把 PDF / PPT 变成带讲稿、配音和镜头调度的讲解视频。

**这个服务不持有模型，讲稿由你来写。** 它负责解析幻灯片、配音、镜头、
时间轴、渲染与质检——这些是确定性的部分。

三步：
1. 上传源文件拿 upload_id：能发普通 HTTP 请求就用 `POST /uploads`（multipart，
   适合大文件）；不能就用 `upload_source` 工具传 base64。然后调 `prepare_project`——
   它返回逐页内容（文字、元素）和**每页的时长/字数预算**。
2. 读完页面，按预算写逐页讲稿，调 `render_video(project_id, narrations)`。
   立即返回 job_id，渲染要数分钟，用 `job_status` 轮询。
3. `project_summary` 看质量分和质检结果；要改就 `revise_scenes` 传新讲稿，
   只会重配音、重渲受影响的那几个场景。

写讲稿时请守住 target_chars：时长是按字数估的，超了成片就会超时长，
而音频一旦生成，长度就改不动了。"""


class StaticTokenVerifier(TokenVerifier):
    """Checks the bearer token against the one configured for this deployment.

    Deliberately the simplest thing that is not a lie: this is a single-tenant
    tool, so there is one token and it either matches or it does not. Anything
    fancier (issuers, scopes, rotation) would be unverified ceremony around the
    same single secret.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._token or token != self._token:
            return None
        return AccessToken(token=token, client_id="doc2video", scopes=[])


def build_server(settings: Settings | None = None) -> MCPServer:
    """Build the MCP server. Tools close over the API process's singletons.

    ``api.deps`` is imported here rather than at module scope: importing the
    api package builds the FastAPI app, which mounts this module — so a caller
    that reaches for ``mcp_server`` first would import a half-built module of
    its own. Deferring the import breaks that cycle whichever side starts it.
    """
    from .api.deps import get_agent, get_jobs

    settings = settings or get_settings()

    mcp = MCPServer(
        "doc2video",
        title="Doc2Video Agent",
        instructions=INSTRUCTIONS,
        version=core_version(),
    )

    @mcp.tool()
    def upload_source(filename: str, content_base64: str) -> dict[str, Any]:
        """Upload a small PDF / PPT / PPTX and get the upload_id to prepare with.

        Args:
            filename: Original name — its suffix decides how the deck is parsed.
            content_base64: The file, base64-encoded.

        For a client that can make an ordinary HTTP request, ``POST /uploads``
        (multipart) is the better path: base64 inflates the file by a third and
        it all travels through the conversation. This tool exists so a client
        with no way out to HTTP is not locked out of step one.
        """
        from .api.routes.uploads import store_upload

        try:
            raw = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError(f"content_base64 不是合法的 base64：{exc}") from None
        return store_upload(filename, io.BytesIO(raw))

    @mcp.tool()
    def prepare_project(brief: str, upload_id: str) -> dict[str, Any]:
        """Parse an uploaded deck and return what you need to write the script.

        Args:
            brief: The video in one sentence — length, audience, which pages
                matter. e.g. "8 分钟的产品讲解视频，面向企业客户，第 5~8 页重点讲".
                Parsed by rule: a duration and emphasised pages are picked up,
                anything else is left at its default.
            upload_id: From ``POST /uploads`` or ``upload_source``. This server
                cannot read your filesystem, so a local path will not work.

        Returns the per-page content **and** ``narration_guide``: the seconds
        and character budget each page has been allocated. Write to those
        budgets — duration is estimated from character count, and once the audio
        exists its length cannot be changed without re-voicing.

        Fast (seconds): no rendering happens here.
        """
        source = _resolve_upload(settings, upload_id)
        return _document_view(get_agent().prepare(source, brief))

    @mcp.tool()
    def render_video(project_id: str, narrations: dict[str, str]) -> dict[str, Any]:
        """Voice, direct, render and check a project using the script you wrote.

        Args:
            project_id: From prepare_project.
            narrations: Page index (as a string) -> that page's script. A page
                you leave out gets placeholder text and is reported back as a
                degradation, so a partial script still renders.

        Returns a job_id immediately; rendering takes minutes. Poll job_status.
        """
        job = get_jobs().submit(
            JobRequest(
                message="按调用方讲稿生成视频",
                project_id=project_id,
                narrations=_page_keys(narrations),
            )
        )
        return {"job_id": job.id, "status": job.status}

    @mcp.tool()
    def revise_scenes(project_id: str, scenes: dict[str, str]) -> dict[str, Any]:
        """Replace the script of specific scenes and re-render only those.

        Args:
            project_id: The project to change.
            scenes: scene_id -> new script. Scene ids come from project_summary.

        Only the named scenes are re-voiced and re-rendered — the rest of the
        video is reused. Returns a job_id; poll job_status.
        """
        job = get_jobs().submit(
            JobRequest(
                message="按调用方讲稿修改场景",
                project_id=project_id,
                scene_narrations=dict(scenes),
            )
        )
        return {"job_id": job.id, "status": job.status}

    @mcp.tool()
    def job_status(job_id: str) -> dict[str, Any]:
        """Where a render_video / revise_scenes job has got to.

        ``status`` is queued | running | succeeded | failed; ``stage`` names the
        pipeline step (parse / understand / narrate / voice / direct / motion /
        render / review). ``queued`` means it is waiting for a render slot.
        """
        job = get_jobs().get(job_id)
        if job is None:
            raise ValueError(f"没有这个任务：{job_id}")
        payload = job.as_dict()
        return {
            "job_id": payload["job_id"],
            "status": payload["status"],
            "stage": payload["stage"],
            "detail": payload["detail"],
            "project_id": payload["project_id"],
            "error": payload["error"],
        }

    @mcp.tool()
    def project_summary(project_id: str) -> dict[str, Any]:
        """Everything worth knowing about one project after a run.

        Includes the quality score with its per-dimension breakdown and the
        review findings — read these before deciding what to change, rather
        than asking the user to watch the video first. The checks are
        structural (missing audio, dangling camera targets, pacing, subtitle
        overflow, how closely a script echoes its page); judging whether the
        script is any *good* is yours to do, from the text returned here.
        """
        project = get_agent().get_project(project_id)
        return {
            "project_id": project.project_id,
            "status": project.status.value,
            "source": project.source.file,
            "title": project.document.title,
            "duration_s": round(project.total_duration(), 1),
            "target_duration_s": project.intent.duration,
            "scene_count": len(project.scenes),
            "output_ready": bool(project.render.output_path),
            "quality": project.quality.model_dump(mode="json") if project.quality else None,
            "review": [f.model_dump(mode="json") for f in project.review],
            "scenes": [
                {
                    "scene_id": scene.scene_id,
                    "source_page": scene.source_page,
                    "duration_s": round(scene.duration, 1),
                    "narration": scene.narration,
                }
                for scene in project.scenes
            ],
        }

    @mcp.tool()
    def list_projects() -> dict[str, Any]:
        """Every project on this server, newest first."""
        return {"items": get_agent().list_projects()}

    @mcp.tool()
    def video_download_path(project_id: str) -> dict[str, Any]:
        """Where to fetch the finished MP4.

        Returns the API path rather than bytes: a video is tens of megabytes and
        has no business travelling through a tool result.
        """
        project = get_agent().get_project(project_id)
        if not project.render.output_path:
            return {"ready": False, "path": None, "hint": "成片尚未生成，先看 job_status"}
        return {"ready": True, "path": f"/projects/{project_id}/video"}

    return mcp


def _document_view(project) -> dict[str, Any]:
    """The deck as the caller needs to see it to write a script.

    Element text is included because a script written from page titles alone
    reads like a table of contents. The budget travels in the same payload so
    there is no second call between reading and writing.
    """
    from .skills import NarrationSkill
    from .skills.base import SkillContext

    ctx = SkillContext.build(project)
    return {
        "project_id": project.project_id,
        "title": project.document.title,
        "topic": project.document.topic,
        "intent": project.intent.model_dump(mode="json"),
        "pages": [
            {
                "index": page.index,
                "title": page.title,
                "page_type": page.page_type.value,
                "summary": page.summary,
                "elements": [
                    {"id": e.id, "kind": e.kind.value, "text": e.text}
                    for e in page.elements
                    if e.text
                ],
            }
            for page in project.document.ordered_pages()
        ],
        "narration_guide": NarrationSkill(ctx).guide(),
    }


def _page_keys(narrations: dict[str, str]) -> dict[int, str]:
    """JSON object keys are strings; page indexes are integers."""
    converted: dict[int, str] = {}
    for key, text in narrations.items():
        try:
            converted[int(key)] = text
        except (TypeError, ValueError):
            raise ValueError(f"页码必须是整数，收到 {key!r}") from None
    return converted


def _resolve_upload(settings: Settings, upload_id: str):
    """Turn an upload_id back into a path, refusing anything that escapes."""
    from .api.routes.uploads import resolve_upload

    try:
        return resolve_upload(settings, upload_id)
    except Doc2VideoError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the model as a tool error
        raise ValueError(f"无法读取上传文件 {upload_id}：{exc}") from exc
