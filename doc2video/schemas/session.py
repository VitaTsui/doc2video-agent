"""The conversation about one video, as something that outlives the process.

A coding agent's transcript *is* its state: drop a turn and you lose the fact
that a file was written. Here it is not. The state is the VideoProject — the
deck, the script, the scenes, the timings, the last quality report — and it is
on disk, complete, independently of anything anyone said.

That difference is what makes compaction safe here. The transcript carries only
*why*: what the person asked for, what was tried, what was rejected and on what
grounds. Summarising the older half of it loses nuance, never state; the next
turn re-reads the project rather than replaying the conversation to reconstruct
it. Anything the agent must not forget belongs in the project, not in a turn.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class Speaker(StrEnum):
    USER = "user"
    AGENT = "agent"
    # A tool's outcome, recorded so the agent can see what its own action did.
    TOOL = "tool"
    # Older turns, replaced by a summary of them.
    SUMMARY = "summary"


class Turn(BaseModel):
    speaker: Speaker
    text: str
    # For TOOL turns: which action, and whether it worked.
    action: str = ""
    ok: bool = True
    at: datetime = Field(default_factory=_now)

    def cost(self) -> int:
        """Rough size in tokens, for deciding when to compact.

        Deliberately crude: counting exactly would need the tokenizer of
        whichever provider is configured, and the decision this feeds — "is the
        transcript getting long" — does not need that precision. CJK runs about
        one token per character, latin about four characters per token; taking
        the pessimistic side keeps the estimate from sliding under the truth.
        """
        return max(1, len(self.text) // 2 + len(self.action) // 2)


class Session(BaseModel):
    """One continuing conversation about one project."""

    project_id: str
    turns: list[Turn] = Field(default_factory=list)
    # How many turns have been folded into summaries so far — reported so a
    # user can tell "the agent forgot" from "the agent never knew".
    compacted: int = 0
    updated_at: datetime = Field(default_factory=_now)

    def cost(self) -> int:
        return sum(turn.cost() for turn in self.turns)

    def recent(self, limit: int) -> list[Turn]:
        return self.turns[-limit:]
