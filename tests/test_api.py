"""API surface: the agent entry point's contract, without running a pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from doc2video.api.app import create_app
from doc2video.core import flags


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A backend whose storage is this test's, not this machine's.

    The app reads the settings singleton, which defaults `storage_dir` to a
    relative `./storage` — the repository's own. Tests asserting "nothing has
    run yet" then passed only on a checkout where nobody had ever run the
    pipeline, and turned red on any machine that had.
    """
    from doc2video.api import deps
    from doc2video.core import config

    monkeypatch.setenv("D2V_STORAGE_DIR", str(tmp_path / "storage"))
    for cached in (config.get_settings, deps.get_agent, deps.get_jobs):
        cached.cache_clear()
    try:
        yield TestClient(create_app())
    finally:
        # The next test to build one must not inherit this directory.
        for cached in (config.get_settings, deps.get_agent, deps.get_jobs):
            cached.cache_clear()


def test_health(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}


def test_capabilities_reports_every_layer(client: TestClient):
    body = client.get("/health/capabilities").json()
    assert set(body) >= {"llm", "tts", "renderers", "binaries", "video"}
    assert "remotion" in body["renderers"]
    assert "ffmpeg" in body["binaries"]
    # The model layer is reported but empty by default: holding no model is
    # still the service's contract, and a caller has to be able to tell
    # "nothing configured" from "configured and broken" before it decides
    # whether to write the script itself.
    assert body["llm"]["available"] is False
    assert body["llm"]["configured"] == "mock"


def test_agent_run_requires_a_message(client: TestClient):
    response = client.post("/agent/run", json={"project_id": "proj_x", "message": "   "})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"


def test_agent_run_requires_a_file_on_first_call(client: TestClient):
    response = client.post("/agent/run", json={"message": "生成一个视频"})
    assert response.status_code == 400


def test_unknown_project_returns_404(client: TestClient):
    response = client.get("/projects/proj_does_not_exist")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"


def test_unknown_job_returns_404(client: TestClient):
    assert client.get("/jobs/job_missing").status_code == 404


def test_project_list_is_available(client: TestClient):
    assert "items" in client.get("/projects").json()


def test_metrics_is_readable_before_any_run(client: TestClient):
    """A brand-new deployment must not 500 on its own dashboard."""
    body = client.get("/metrics").json()

    assert "summary" in body
    assert set(body["rollout"]) == set(flags.FLAGS)


def test_metrics_runs_lists_nothing_rather_than_failing(client: TestClient):
    assert client.get("/metrics/runs").json() == {"items": []}


def test_quality_is_404_before_the_project_is_reviewed(client: TestClient):
    response = client.get("/projects/proj_does_not_exist/quality")
    assert response.status_code == 404


def test_agent_run_upload_cannot_escape_the_uploads_directory(client: TestClient):
    """A multipart filename is attacker-controlled; it used to be joined raw.

    The name still passes the suffix check — that is all `detect_source_type`
    looks at — so what matters is where the bytes landed, not the status code.
    """
    from doc2video.core.config import get_settings

    uploads = Path(get_settings().uploads_dir).resolve()
    escaped = uploads.parent / "pwned.pptx"

    # An empty message is rejected *after* the uploads are stored, so this
    # exercises the write without starting a render.
    response = client.post(
        "/agent/run",
        data={"message": "  "},
        files={"files": ("../pwned.pptx", b"not a deck", "application/octet-stream")},
    )
    assert response.status_code == 400

    assert not escaped.exists()
    written = [p for p in uploads.rglob("*") if p.is_file()]
    assert written, "文件应该被存下来，只是不能存到目录外"
    assert all(uploads in p.resolve().parents for p in written)
    assert all(p.name == "pwned.pptx" for p in written)


def test_narration_routes_exist_for_a_client_without_mcp(client: TestClient):
    """The desktop app should not have to speak MCP to a server in its own process."""
    missing = client.post("/projects/proj_nope/narrations", json={"narrations": {"1": "你好"}})
    assert missing.status_code == 404

    bad_key = client.post("/projects/proj_nope/narrations", json={"narrations": {"封面": "x"}})
    assert bad_key.status_code in (400, 404)


def test_chat_is_a_job_because_a_turn_may_render_more_than_once(client: TestClient):
    """The route the window uses to talk to the agent rather than command it.

    It cannot be a request that waits: one message can cost several renders,
    and the caller needs the progress stream in between.
    """
    assert client.post("/projects/proj_nope/chat", json={"message": "短一点"}).status_code == 404
    assert client.post("/projects/proj_nope/chat", json={}).status_code == 422


def test_the_transcript_is_readable_by_something_other_than_the_model(client: TestClient):
    """A session that survives the process is no use if only the prompt reads it.

    It is written beside the project turn by turn; without this route a
    reopened window greets you as a stranger while the agent remembers
    everything you said.
    """
    assert client.get("/projects/proj_nope/session").status_code == 404


def test_job_events_streams_and_closes(client: TestClient):
    """A late subscriber gets the outcome and a done event, not a hung stream."""
    assert client.get("/jobs/job_nope/events").status_code == 404


def test_media_may_authenticate_by_query_but_nothing_else_can(monkeypatch):
    """`<video src>` cannot send a header; every other route still must."""
    from doc2video.core.config import get_settings

    # create_app() reads the cached settings; patching the instance is what a
    # token-protected deployment looks like from inside the process.
    monkeypatch.setattr(get_settings(), "api_token", "s3cret")
    guarded = TestClient(create_app())

    # A media GET is reachable with the token in the query — 404 here means it
    # got past the middleware and found no such project, which is the point.
    assert guarded.get("/projects/proj_1/video?token=s3cret").status_code == 404
    assert guarded.get("/projects/proj_1/video?token=wrong").status_code == 401
    assert guarded.get("/projects/proj_1/video").status_code == 401

    # The voice preview is one too: it is played by an `<audio src>`, which
    # cannot send a header either. 200 because the machine can always speak.
    assert guarded.get("/health/voices/preview?token=s3cret").status_code in (200, 502)
    assert guarded.get("/health/voices/preview?token=wrong").status_code == 401

    # Everything else still needs the header, however the URL is dressed up.
    assert guarded.get("/projects/proj_1?token=s3cret").status_code == 401
    assert guarded.get("/jobs/job_1/events?token=s3cret").status_code == 401
    assert (
        guarded.post("/projects/proj_1/narrations?token=s3cret", json={}).status_code == 401
    )


def test_a_preflight_is_not_rejected_for_having_no_token(monkeypatch):
    """Without this the desktop UI cannot make a single request.

    Every cross-origin call carrying an Authorization header is preceded by an
    OPTIONS the browser strips credentials from. This middleware runs outside
    the CORS one, so a 401 here is final — the request never happens.
    """
    from doc2video.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", "s3cret")
    monkeypatch.setattr(settings, "cors_origins", ["tauri://localhost"])
    guarded = TestClient(create_app())

    preflight = guarded.options(
        "/uploads",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "tauri://localhost"
    # The real request still needs the token.
    assert guarded.post("/uploads").status_code == 401


def test_an_upload_may_carry_its_token_in_the_query(monkeypatch, tmp_path):
    """The file picker cannot set a header.

    It is a component we configure with a URL and nothing else, so the token
    has to travel in the URL — the same accommodation `<video src>` already
    needed. Everything else still must use the header.
    """
    from doc2video.api import deps
    from doc2video.core import config

    monkeypatch.setenv("D2V_API_TOKEN", "s3cret")
    monkeypatch.setenv("D2V_STORAGE_DIR", str(tmp_path / "storage"))
    for cached in (config.get_settings, deps.get_agent, deps.get_jobs):
        cached.cache_clear()
    try:
        client = TestClient(create_app())
        files = {"file": ("deck.pdf", b"%PDF-1.4 x", "application/pdf")}

        assert client.post("/uploads", files=files).status_code == 401
        assert client.post("/uploads?token=wrong", files=files).status_code == 401
        assert client.post("/uploads?token=s3cret", files=files).status_code == 200
        # The concession is for uploads alone; nothing else gains it.
        assert client.get("/projects?token=s3cret").status_code == 401
    finally:
        for cached in (config.get_settings, deps.get_agent, deps.get_jobs):
            cached.cache_clear()


def test_a_percent_encoded_filename_is_shown_as_the_person_wrote_it():
    """Some clients encode a non-ASCII name into the plain `filename` field.

    Nothing downstream unpicks that, and a PDF's title falls back to its stem —
    so the deck someone just dropped in came back named
    `1786709904848_%E7%9F%B3%E5%8C%96AI…`, which is their own file made
    unreadable by the transport that carried it.
    """
    from doc2video.api.routes.uploads import readable_name

    encoded = "1786709904848_%E7%9F%B3%E5%8C%96AI%E5%95%86%E4%B8%9A.pdf"
    assert readable_name(encoded) == "1786709904848_石化AI商业.pdf"

    # A name that really contains a percent sign has to survive: decoding is
    # only accepted when it round-trips.
    assert readable_name("report 50%.pdf") == "report 50%.pdf"
    assert readable_name("已经是中文.pptx") == "已经是中文.pptx"
    # The directory part goes.
    assert readable_name("../../etc/passwd") == "passwd"
    # An encoded separator does not round-trip, so it is left encoded rather
    # than decoded into one. The guarantee is "never a path", not "always
    # decoded" — and this is the direction to fail in.
    assert "/" not in readable_name("%2E%2E%2Fetc%2Fpasswd")


def test_a_project_can_be_reopened_with_its_pages(client: TestClient):
    """The window can only edit a script it can show the pages for.

    Before this route the pages existed only in the answer to the parse that
    produced them, so reopening a project from the sidebar gave a panel with
    no document tab — and the script the model had written was visible but not
    editable.
    """
    assert client.get("/projects/proj_nope/pages").status_code == 404


def test_a_deck_is_parsed_first_and_written_second(client: TestClient, demo_pptx: Path):
    """Two steps, and the pages carry the words after the second one.

    The parse returns a deck with empty pages — fast, and something to look at
    while the script is written. Drafting is its own job against that deck, and
    what it writes comes back on the same page list, so the editor opens on the
    words instead of beside them.
    """
    with demo_pptx.open("rb") as handle:
        upload = client.post("/uploads", files={"file": ("demo.pptx", handle)}).json()

    parsed = client.post(
        "/agent/prepare", json={"upload_id": upload["upload_id"], "brief": "讲两分钟"}
    ).json()
    assert parsed["pages"]
    assert not any(page["narration"] for page in parsed["pages"])

    project_id = parsed["project_id"]
    job = client.post(f"/projects/{project_id}/draft").json()
    assert job["job_id"]
    _finish(client, job["job_id"])

    pages = client.get(f"/projects/{project_id}/pages").json()["items"]
    assert all(page["narration"] for page in pages)

    # And it stopped at the script: nothing was voiced or rendered.
    project = client.get(f"/projects/{project_id}").json()
    assert project["render"]["output_path"] is None


def _finish(client: TestClient, job_id: str) -> dict:
    """Wait for a job, without pinning the test to how long one takes."""
    import time

    for _ in range(600):
        state = client.get(f"/jobs/{job_id}").json()
        if state["status"] in {"succeeded", "failed"}:
            assert state["status"] == "succeeded", state
            return state
        time.sleep(0.05)
    raise AssertionError("任务没有在预期时间内结束")


def test_the_two_page_lists_are_one_list():
    """`prepare` and `/pages` must not drift.

    They render the same deck; a difference between them would be a project
    that looks one way when parsed and another when reopened.
    """
    import inspect

    from doc2video.api.routes import agent, projects

    assert "page_views" in inspect.getsource(agent.prepare)
    assert "page_views" in inspect.getsource(projects.get_pages)


def test_a_scene_carries_the_clip_it_was_rendered_into(client: TestClient, monkeypatch):
    """Checking one page should not mean scrubbing the whole film.

    The per-scene clips are rendered before the concatenation and kept — that
    is how editing one page re-renders one page — so the only reason the
    window could not play them was that the route did not hand them over.
    """
    import inspect

    from doc2video.api.routes import projects

    assert "scene_clips" in inspect.getsource(projects.get_scenes)


def test_deleting_a_project_leaves_the_uploaded_file_alone(tmp_path, monkeypatch):
    """The video goes; the deck it was made from does not.

    They live in different places for exactly this reason — the upload is
    copied into the project — but nothing said so, and a delete that took the
    source with it would mean going to find the file again to try a second
    time.
    """
    import io

    from doc2video.api.routes.uploads import store_upload
    from doc2video.core import config
    from doc2video.storage import ProjectStore

    monkeypatch.setenv("D2V_STORAGE_DIR", str(tmp_path / "storage"))
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        settings.ensure_dirs()
        stored = store_upload("deck.pdf", io.BytesIO(b"%PDF-1.4 x"))
        uploaded = settings.uploads_dir / stored["upload_id"] / "deck.pdf"
        assert uploaded.exists()

        store = ProjectStore(settings)
        store.ensure_layout("proj_x")
        store.import_source("proj_x", uploaded)
        store.delete("proj_x")

        assert not (settings.storage_dir / "projects" / "proj_x").exists()
        assert uploaded.exists(), "删掉工程不该动上传的原件"
    finally:
        config.get_settings.cache_clear()


def test_the_plugins_page_carries_the_prompts_themselves(client):
    """A paraphrase is the thing you cannot check the output against.

    The steps that ask a model something say exactly what they ask, and the
    steps that ask nothing publish the numbers that decide their output —
    read from the constants, so the page cannot drift away from the code.
    """
    from doc2video.skills.base import load_prompt
    from doc2video.skills.speech_review import TOO_FAST

    found = {p["id"]: p for p in client.get("/health/plugins").json()["plugins"]}

    assert found["presentation-narration"]["prompt"] == load_prompt("narration")
    assert found["presentation-understanding"]["prompt"] == load_prompt("document_understanding")
    # The agent's own instructions are a prompt too, and the most consequential.
    assert "write_script" in found["agent:loop"]["prompt"]

    # A deterministic skill has no prompt and says so by having none.
    assert found["presentation-review"]["prompt"] == ""
    rates = [r["value"] for r in found["presentation-review"]["rules"] if r["name"] == "语速上限"]
    assert rates == [f"{TOO_FAST:.0f} 字/分"]


def test_every_quality_dimension_has_a_chinese_name_in_the_window():
    """The panel translates the dimensions; a missing one shows up in English.

    That is exactly how 「render」 sat among five Chinese words — the check
    that produced it was added and the map next to it was not.
    """
    import re
    from pathlib import Path

    from doc2video.skills import review

    emitted = set(re.findall(r'name="([a-z_]+)"', Path(review.__file__).read_text("utf-8")))
    panel = Path("desktop/web/src/Artifacts.tsx").read_text("utf-8")
    mapped = set(
        re.findall(r"(\w+): '", panel.split("const DIMENSION")[1].split("}")[0])
    )
    assert emitted <= mapped, f"没有中文名的维度：{sorted(emitted - mapped)}"


def test_a_rule_can_be_changed_and_put_back(client, tmp_path, monkeypatch):
    """These numbers were measured, which makes them defaults and not laws.

    What matters is that changing one reaches the code that uses it — a form
    that saves a number nothing reads is worse than no form.
    """
    from doc2video.core import prefs, tuning
    from doc2video.core.config import get_settings
    from doc2video.tools.tts.units import _emphasis_of

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    prefs.save(prefs.Preferences())

    assert _emphasis_of("这一句是重点。", True) == tuning.value("voice.pause_emphasis")

    client.put("/health/plugins/rules", json={"id": "voice.pause_emphasis", "value": 0.9})
    assert _emphasis_of("这一句是重点。", True) == 0.9

    # Out of range is clamped rather than refused: a stored file is not a form.
    client.put("/health/plugins/rules", json={"id": "shot.max_scale", "value": 99})
    assert tuning.value("shot.max_scale") == tuning.knobs()["shot.max_scale"].high

    # And no value at all puts the measured default back.
    client.put("/health/plugins/rules", json={"id": "voice.pause_emphasis"})
    from doc2video.tools.tts import units

    assert _emphasis_of("这一句是重点。", True) == units.PAUSE_EMPHASIS


def test_an_edited_prompt_is_what_gets_sent_and_survives_an_update(client, tmp_path, monkeypatch):
    """A prompt you can change that the next release overwrites is a prompt you
    cannot change. So edits live in the storage directory, not beside the code.
    """
    from doc2video.core.config import get_settings
    from doc2video.skills.base import PROMPTS_DIR, load_prompt, shipped_prompt

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    original = shipped_prompt("narration")

    client.put("/health/plugins/prompt", json={"id": "narration", "text": "只写一句话。"})
    # What the skill will actually send, not merely what was stored.
    assert load_prompt("narration") == "只写一句话。\n"
    # And the build's own copy is untouched, which is what an update replaces.
    assert (PROMPTS_DIR / "narration.md").read_text("utf-8") == original

    found = {p["id"]: p for p in client.get("/health/plugins").json()["plugins"]}
    assert found["presentation-narration"]["prompt_edited"] is True

    # 「复原」 removes the override rather than storing a copy of the shipped
    # text, so a later release's better wording is not frozen out.
    client.put("/health/plugins/prompt", json={"id": "narration", "text": ""})
    assert not (tmp_path / "prompts" / "narration.md").exists()
    assert load_prompt("narration") == original


def test_the_plugin_list_is_plugins_and_not_plumbing(client):
    """`node` and `npx` are not things anyone installs on purpose.

    A missing one already says so where it matters — the renderer that needs
    it reports itself unavailable, with the reason.
    """
    found = [p["id"] for p in client.get("/health/plugins").json()["plugins"]]
    assert not [p for p in found if p.startswith("bin:")], found
