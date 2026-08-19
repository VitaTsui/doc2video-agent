"""Recording the chain as it happens — what ran, with what, and what came out.

Not a ledger in the accounting sense, whatever the filename says: it is the
record of one execution, ordered, with each step's outputs and the tools it
reached for.

Shaped after ``core/telemetry``: a recorder in a ContextVar, so a step deep in
the pipeline can name what it produced without every function between here and
there carrying a recorder argument. Jobs run one per thread and each thread
gets its own copy, so two renders cannot write into each other's account.

Append-only on disk, one JSON object per line. A render that dies halfway
leaves everything up to that point readable, which is exactly when someone
wants to read it — a ledger that only becomes valid at the end is no use for
the failures it exists to explain.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from ..schemas.ledger import Artifact, ArtifactKind, EntryKind, LedgerEntry
from .logging import get_logger

log = get_logger(__name__)

LEDGER_FILE = "ledger.jsonl"

# One project's ledger is read whole by the UI; a long-lived project with many
# edits would otherwise grow without bound.
MAX_ENTRIES = 2000

_current: ContextVar[Recorder | None] = ContextVar("doc2video_ledger", default=None)

# What the step running right now has reached for. A ContextVar for the same
# reason the recorder is one: the tool that knows its own name is several
# frames below the step that will report it, and threading an argument through
# every one of them to carry a label is not worth it.
_tools: ContextVar[list[str] | None] = ContextVar("doc2video_ledger_tools", default=None)


class Recorder:
    """Writes one project's ledger. Safe to share across a run's threads."""

    def __init__(self, path: Path, run_id: str = "") -> None:
        self._path = path
        self._run_id = run_id
        self._lock = threading.Lock()
        self._seq = _last_seq(path)

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def run_scope(self, run_id: str) -> Iterator[None]:
        """Tag entries with a different run for the duration of a block.

        A chat turn is one account containing several runs: the agent decides,
        a render happens under its own run id, then it decides again. The
        entries stay in one numbered sequence; only their run tag changes.
        """
        previous = self._run_id
        self._run_id = run_id or previous
        try:
            yield
        finally:
            self._run_id = previous

    def record(
        self,
        kind: EntryKind,
        name: str,
        *,
        detail: str = "",
        status: str = "ok",
        duration_s: float = 0.0,
        artifacts: list[Artifact] | None = None,
        tools: list[str] | None = None,
    ) -> LedgerEntry:
        with self._lock:
            self._seq += 1
            entry = LedgerEntry(
                seq=self._seq,
                kind=kind,
                name=name,
                detail=detail,
                status=status,
                duration_s=duration_s,
                artifacts=artifacts or [],
                tools=tools or [],
                run_id=self._run_id,
            )
            self._append(entry)
        return entry

    @contextmanager
    def stage(self, name: str, detail: str = "") -> Iterator[list[Artifact]]:
        """Time one step and record whatever it appends to the yielded list.

        The list is the step's way of saying what it made: it appends as it
        goes, and a failure still records everything produced before it, which
        is usually the most useful thing in the file.
        """
        artifacts: list[Artifact] = []
        started = time.monotonic()
        status = "ok"
        token = _tools.set([])
        try:
            yield artifacts
        except Exception as exc:
            status = "failed"
            detail = detail or str(exc)[:200]
            raise
        finally:
            used = _tools.get() or []
            _tools.reset(token)
            self.record(
                EntryKind.STAGE,
                name,
                detail=detail,
                status=status,
                duration_s=time.monotonic() - started,
                artifacts=artifacts,
                tools=used,
            )

    def _append(self, entry: LedgerEntry) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(entry.model_dump_json() + "\n")
        except OSError as exc:
            # An unwritable ledger must never take down the render it is
            # describing; losing the account is bad, losing the video is worse.
            log.debug("无法写入执行记录：%s", exc)


def _last_seq(path: Path) -> int:
    """Continue numbering across runs, so a project's chain reads in order."""
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


# -- module-level access ---------------------------------------------------
@contextmanager
def recording(path: Path, run_id: str = "") -> Iterator[Recorder]:
    active = _current.get()
    if active is not None and active.path == path:
        # A chat turn records, and every render it causes records the same
        # project. A second recorder would start numbering from its own read of
        # the file and collide with the first, so nest instead of stacking.
        with active.run_scope(run_id):
            yield active
        return

    recorder = Recorder(path, run_id)
    token = _current.set(recorder)
    try:
        yield recorder
    finally:
        _current.reset(token)


def current() -> Recorder | None:
    """The active recorder, or None outside a run (tests, CLI inspection)."""
    return _current.get()


def note(name: str, detail: str = "", **kwargs) -> None:
    """Record something worth seeing, if anyone is recording."""
    recorder = _current.get()
    if recorder is not None:
        recorder.record(EntryKind.NOTE, name, detail=detail, **kwargs)


def decision(name: str, detail: str, artifacts: list[Artifact] | None = None) -> None:
    """Record why the agent did what it did next."""
    recorder = _current.get()
    if recorder is not None:
        recorder.record(EntryKind.DECISION, name, detail=detail, artifacts=artifacts)


def used(tool: str) -> None:
    """Name a tool the current step reached for. Deduplicated, order kept.

    Safe to call from anywhere, including outside a run: a tool should not have
    to know whether anyone is watching.
    """
    tools = _tools.get()
    if tools is not None and tool and tool not in tools:
        tools.append(tool)


def degradation(name: str, detail: str) -> None:
    recorder = _current.get()
    if recorder is not None:
        recorder.record(EntryKind.DEGRADATION, name, detail=detail, status="skipped")


def read(path: Path, limit: int = MAX_ENTRIES) -> list[LedgerEntry]:
    """The account so far, oldest first."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    entries: list[LedgerEntry] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            entries.append(LedgerEntry.model_validate_json(line))
        except ValueError:
            continue  # a truncated last line from a killed process
    return entries


# -- helpers for the steps that produce things -----------------------------
def file_artifact(label: str, path: str, kind: ArtifactKind, scene_id: str = "") -> Artifact:
    return Artifact(label=label, kind=kind, path=path, scene_id=scene_id)


def text_artifact(label: str, text: str, scene_id: str = "") -> Artifact:
    return Artifact(label=label, kind=ArtifactKind.TEXT, text=text, scene_id=scene_id)
