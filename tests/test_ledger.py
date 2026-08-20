"""The account of how a video got made.

Telemetry answers an operator's questions. This answers the one the person
watching a render has — what did that step produce, and can I look at it — so
what matters here is that every step names its outputs and that a failure still
leaves everything up to it readable.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from doc2video.core import ledger
from doc2video.schemas.ledger import ArtifactKind, EntryKind


def test_a_step_records_what_it_made(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    with ledger.recording(path, "run_1") as recorder:
        with recorder.stage("配音") as artifacts:
            artifacts.append(
                ledger.file_artifact("第 1 页配音", "audio/scene_01.wav", ArtifactKind.AUDIO)
            )

    (entry,) = ledger.read(path)
    assert entry.name == "配音"
    assert entry.status == "ok"
    assert entry.artifacts[0].path == "audio/scene_01.wav"
    assert entry.run_id == "run_1"


def test_a_failed_step_keeps_what_it_produced_first(tmp_path: Path):
    """The most useful thing in the file is usually what the failing step had
    already made before it failed."""
    path = tmp_path / "ledger.jsonl"
    with ledger.recording(path):
        try:
            with ledger.current().stage("渲染合成") as artifacts:
                artifacts.append(
                    ledger.file_artifact("第 1 页片段", "clips/scene_01.mp4", ArtifactKind.VIDEO)
                )
                raise RuntimeError("ffmpeg 挂了")
        except RuntimeError:
            pass

    (entry,) = ledger.read(path)
    assert entry.status == "failed"
    assert "ffmpeg" in entry.detail
    assert len(entry.artifacts) == 1


def test_numbering_continues_across_runs(tmp_path: Path):
    """A project edited three times should read as one chain, in order."""
    path = tmp_path / "ledger.jsonl"
    for run in ("run_1", "run_2"):
        with ledger.recording(path, run) as recorder:
            recorder.record(EntryKind.STAGE, "解析文档")

    assert [e.seq for e in ledger.read(path)] == [1, 2]


def test_a_truncated_last_line_does_not_lose_the_rest(tmp_path: Path):
    """A killed process leaves a half-written line; the account before it is
    still the point."""
    path = tmp_path / "ledger.jsonl"
    with ledger.recording(path) as recorder:
        recorder.record(EntryKind.STAGE, "配音")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "kind": "stage", "na')

    entries = ledger.read(path)
    assert len(entries) == 1
    assert entries[0].name == "配音"


def test_an_unwritable_ledger_never_takes_down_the_render(tmp_path: Path):
    """Losing the account is bad; losing the video is worse."""
    with ledger.recording(tmp_path / "nope" / "deep" / "x" / "ledger.jsonl") as recorder:
        recorder._path = Path("/proc/definitely-not-writable/ledger.jsonl")
        recorder.record(EntryKind.NOTE, "还是要继续")  # must not raise


def test_recording_outside_a_run_is_a_no_op():
    """Tests and CLI inspection run outside any recording context."""
    ledger.note("没人在记")
    ledger.decision("也没人在记", "所以什么也不会发生")
    assert ledger.current() is None


def test_every_call_is_recorded_under_the_step_that_made_it(tmp_path):
    """A stage says a deck was voiced; the calls say which scene took eight seconds.

    Flat, they would be useless: a thirty-scene render writes thirty entries
    that sit as peers of 「解析文档」 and the shape of the run disappears. So a
    stage claims its number before its body runs, and every call inside names
    it.
    """
    path = tmp_path / "ledger.jsonl"
    with ledger.recording(path, "run_1") as recorder:
        with recorder.stage("配音", skill="presentation-voice"):
            for index in (1, 2):
                with ledger.call("tts:mock", f"第 {index} 页"):
                    pass
        with recorder.stage("渲染合成"):
            with ledger.call("renderer:ffmpeg", "scene_01"):
                pass

    entries = ledger.read(path)
    stages = {e.name: e for e in entries if e.kind == EntryKind.STAGE}
    calls = [e for e in entries if e.kind == EntryKind.CALL]

    assert len(calls) == 3
    assert [c.parent for c in calls[:2]] == [stages["配音"].seq] * 2
    assert calls[2].parent == stages["渲染合成"].seq
    assert stages["配音"].skill == "presentation-voice"
    # The tool a call went through is still named on the step, so the summary
    # line does not have to walk the calls to say what did the work.
    assert stages["配音"].tools == ["tts:mock"]


def test_a_failed_call_is_kept_and_says_so(tmp_path):
    """The call that failed is the one worth finding, so it must be written."""
    path = tmp_path / "ledger.jsonl"
    with ledger.recording(path, "run_1") as recorder, contextlib.suppress(RuntimeError):
        with recorder.stage("配音"):
            with ledger.call("tts:mock", "第 3 页"):
                raise RuntimeError("合成失败")

    calls = [e for e in ledger.read(path) if e.kind == EntryKind.CALL]
    assert len(calls) == 1
    assert calls[0].status == "failed"
    assert "合成失败" in calls[0].detail
