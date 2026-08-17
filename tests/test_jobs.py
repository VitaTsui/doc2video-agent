"""Job admission: a render occupies a CPU for minutes, so it has to be bounded."""

from __future__ import annotations

import threading
import time

import pytest

from doc2video.agent.jobs import JobManager, JobRequest
from doc2video.core.config import Settings
from doc2video.core.errors import TooBusy


class _SlowAgent:
    """Stands in for the pipeline: records overlap, takes its time."""

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0
        self._lock = threading.Lock()
        self.release = threading.Event()

    def run(self, **kwargs):  # noqa: ANN003
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        self.release.wait(timeout=5)
        with self._lock:
            self.running -= 1

        class _Result:
            project_id = "proj_1"
            summary = "done"
            __dict__ = {"project_id": "proj_1"}

        return _Result()


def test_renders_do_not_run_all_at_once():
    """Unbounded, ten submissions would be ten Chromiums on the same CPU."""
    agent = _SlowAgent()
    jobs = JobManager(agent, Settings(max_concurrent_jobs=1, max_queued_jobs=50))

    for _ in range(5):
        jobs.submit(JobRequest(message="x"))
    time.sleep(0.3)
    peak_while_busy = agent.peak
    agent.release.set()

    assert peak_while_busy == 1


def test_a_full_queue_is_refused_rather_than_promised():
    """A job that will not start for an hour is more useful as an error.

    The cap counts jobs *waiting for a slot*, so one running plus two queued is
    the limit here — the fourth submission is the one with nowhere to go.
    """
    agent = _SlowAgent()
    jobs = JobManager(agent, Settings(max_concurrent_jobs=1, max_queued_jobs=2))

    jobs.submit(JobRequest(message="x"))
    time.sleep(0.2)  # let it take the slot, so it stops counting as waiting
    for _ in range(2):
        jobs.submit(JobRequest(message="x"))

    with pytest.raises(TooBusy):
        jobs.submit(JobRequest(message="x"))
    agent.release.set()


def test_a_queued_job_is_visible_while_it_waits():
    """Otherwise a polling client cannot tell queued from lost."""
    agent = _SlowAgent()
    jobs = JobManager(agent, Settings(max_concurrent_jobs=1, max_queued_jobs=10))

    first = jobs.submit(JobRequest(message="x"))
    second = jobs.submit(JobRequest(message="x"))
    time.sleep(0.2)
    statuses = (first.status, second.status)
    agent.release.set()

    assert statuses == ("running", "queued")
