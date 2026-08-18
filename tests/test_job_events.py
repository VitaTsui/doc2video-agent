"""Live progress, and the countable work behind it.

Before this the only way to follow a run was to poll `GET /jobs/{id}`, which
reads badly against this pipeline: progress moves in a handful of discrete
steps and rendering can sit on one of them for minutes. A client could not tell
"working" from "stuck".
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import pytest

from doc2video.agent.jobs import Job, JobManager, JobRequest


@dataclass
class _StubResult:
    project_id: str = "proj_stub"
    summary: str = "完成"


class _StubAgent:
    """Stands in for the pipeline: waits to be released, then reports progress.

    It blocks first so a test can attach a watcher before any event fires —
    otherwise the whole job finishes between submit() and watch() and the test
    is really only checking the late-subscriber path.
    """

    def __init__(self, steps: int = 3) -> None:
        self.steps = steps
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, *, progress=None, **kwargs):  # noqa: ARG002
        self.started.set()
        self.release.wait(timeout=5)
        for done in range(self.steps):
            progress("render", f"渲染场景 {done}", done, self.steps)
        return _StubResult()


def _manager() -> tuple[JobManager, _StubAgent]:
    agent = _StubAgent()
    return JobManager(agent), agent


def _drain(channel: queue.SimpleQueue, *, limit: float = 5.0) -> list[dict]:
    events, deadline = [], time.monotonic() + limit
    while time.monotonic() < deadline:
        payload = channel.get(timeout=limit)
        if payload is None:
            return events
        events.append(payload)
    raise AssertionError("没有等到结束事件")


def test_a_watcher_sees_every_step_and_a_close(tmp_path):  # noqa: ARG001
    manager, agent = _manager()
    job = manager.submit(JobRequest(message="做视频"))
    assert agent.started.wait(timeout=5)
    channel = manager.watch(job.id)
    assert channel is not None
    agent.release.set()

    events = _drain(channel)

    assert events[0]["job_id"] == job.id
    rendering = [e for e in events if e["stage"] == "render"]
    assert [e["done"] for e in rendering] == [0, 1, 2]
    assert all(e["total"] == 3 for e in rendering)
    assert events[-1]["status"] in ("succeeded", "failed")


def test_subscribing_after_the_job_finished_still_reports_the_outcome(tmp_path):  # noqa: ARG001
    """A UI that reconnects must not wait for an event that already happened."""
    manager, agent = _manager()
    job = manager.submit(JobRequest(message="做视频"))
    agent.release.set()
    for _ in range(200):
        if job.status in ("succeeded", "failed"):
            break
        time.sleep(0.01)

    events = _drain(manager.watch(job.id))
    assert len(events) == 1
    assert events[0]["status"] == job.status


def test_a_vanished_watcher_cannot_hold_up_the_render():
    """Publishing never blocks: the queue is unbounded and nothing is awaited."""
    manager, _ = _manager()
    job = Job(id="job_x", request=JobRequest(message="x"))
    job.watchers.append(queue.SimpleQueue())
    manager._jobs[job.id] = job

    manager._publish(job, final=True)  # would deadlock if it waited on readers
    assert job.watchers == []


def test_progress_carries_a_denominator_only_where_one_exists():
    manager, agent = _manager()
    job = manager.submit(JobRequest(message="做视频"))
    assert agent.started.wait(timeout=5)
    channel = manager.watch(job.id)
    agent.release.set()
    events = _drain(channel)

    # The stub only reports render steps; the seeded first event has no unit.
    assert events[0]["total"] == 0
    assert any(e["total"] == 3 for e in events)


@pytest.mark.parametrize("missing", ["job_nope"])
def test_watching_an_unknown_job_is_not_an_event_stream(missing: str):
    assert _manager()[0].watch(missing) is None
