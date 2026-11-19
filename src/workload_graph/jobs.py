"""In-process job with a sequenced event log that a stream can wait on.

Status: partial
Purpose: one run over one or more accounts. It records progress as events and holds the build
    result until the TAM has reviewed it.
Inputs: job id, account ids in scope, an injected clock.
Outputs: ``Job``, ``JobEvent``, ``JobState``.
Planned dependencies: threading, pipeline result models.
Notes:
    - Events are numbered from 1. A reader asks for events after the last number it has seen, so
      a dropped connection loses nothing.
    - Event details carry codes only (for example ``AccessDenied``), never exception messages,
      because messages can contain account ids or ARNs.
    - ``AWAITING_REVIEW`` and ``FAILED`` are settled. A stream ends when the job settles.
    - Persistence lives in ``runstore.py``. ``restore`` rebuilds a job from what was saved.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from workload_graph.models import BuildResult


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BUILDING = "building"
    AWAITING_REVIEW = "awaiting_review"
    FAILED = "failed"


_SETTLED = frozenset({JobState.AWAITING_REVIEW, JobState.FAILED})
DECISIONS = ("approve", "reject")


@dataclass(frozen=True)
class JobEvent:
    seq: int
    at: datetime
    kind: str
    account_id: str | None
    detail: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Job:
    """Thread-safe progress log for one run."""

    def __init__(self, job_id: str, accounts: tuple[str, ...],
                 clock: Callable[[], datetime] = _utc_now, region: str = "") -> None:
        self.job_id = job_id
        self.accounts = accounts
        self.region = region
        self._clock = clock
        self.created_at = clock()
        self._cond = threading.Condition()
        self._events: list[JobEvent] = []
        self._state = JobState.QUEUED
        self._result: BuildResult | None = None
        self._decisions: dict[str, str] = {}

    @classmethod
    def restore(cls, job_id: str, accounts: tuple[str, ...], region: str, created_at: datetime,
                state: JobState, events: list[JobEvent], result: BuildResult | None,
                decisions: dict[str, str]) -> Job:
        """Rebuild a saved job. A job saved before it settled comes back ``FAILED``."""
        job = cls(job_id, accounts, region=region)
        job.created_at = created_at
        job._events = list(events)
        job._result = result
        job._decisions = dict(decisions)
        job._state = state
        if state not in _SETTLED:
            job.transition(JobState.FAILED, "failed", "interrupted")
        return job

    @property
    def state(self) -> JobState:
        with self._cond:
            return self._state

    @property
    def result(self) -> BuildResult | None:
        with self._cond:
            return self._result

    @property
    def is_settled(self) -> bool:
        return self.state in _SETTLED

    def emit(self, kind: str, account_id: str | None = None, detail: str = "") -> JobEvent:
        """Append one event and wake any reader waiting on the log."""
        with self._cond:
            event = JobEvent(len(self._events) + 1, self._clock(), kind, account_id, detail)
            self._events.append(event)
            self._cond.notify_all()
            return event

    def transition(self, state: JobState, kind: str, detail: str = "") -> JobEvent:
        """Move to ``state`` and record why in the same event."""
        with self._cond:
            self._state = state
        return self.emit(kind, None, detail)

    def set_result(self, result: BuildResult) -> None:
        with self._cond:
            self._result = result

    @property
    def decisions(self) -> dict[str, str]:
        with self._cond:
            return dict(self._decisions)

    def decide(self, workload_id: str, decision: str) -> None:
        """Record ``approve`` or ``reject`` for one workload. Only after the build has settled.

        Approving or rejecting is the TAM's gate. Cost review runs only after approval (later step).
        """
        with self._cond:
            if self._state is not JobState.AWAITING_REVIEW or self._result is None:
                raise ValueError("no workloads are awaiting review")
            if decision not in DECISIONS:
                raise ValueError("decision must be approve or reject")
            known = {workload.workload_id for workload in self._result.workloads}
            if workload_id not in known:
                raise ValueError("unknown workload")
            self._decisions[workload_id] = decision
            self.emit("decided", None, f"{workload_id}:{decision}")

    def events_after(self, seq: int) -> list[JobEvent]:
        """Every event whose number is greater than ``seq``."""
        with self._cond:
            return self._events[seq:]

    def wait_for_events(self, after: int, timeout: float) -> list[JobEvent]:
        """Block until there is an event after ``after``, the job settles, or time runs out."""
        with self._cond:
            self._cond.wait_for(
                lambda: len(self._events) > after or self._state in _SETTLED,
                timeout=timeout,
            )
            return self._events[after:]
