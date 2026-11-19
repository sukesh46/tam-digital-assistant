"""Shared plumbing for the two cost collectors.

Status: real
Purpose: run one labelled call at a time, record its status, and build the cost snapshot.
Inputs: a ``CallRecorder`` and an operation label.
Outputs: ``LabelledRun`` and ``cost_snapshot``.
Planned dependencies: collectors.base, cost.labels.
Notes: every label gets one ``_labels`` record (``ok``, ``denied``, ``not_enabled``, ``partial``,
    ``error`` or ``skipped``) with the error code only, never a message.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from workload_graph.collectors.base import (
    BudgetExceededError,
    CallRecorder,
    NotEnabledError,
    OperationDeniedError,
    OperationFailedError,
    ThrottledOutError,
)
from workload_graph.cost.labels import LABELS_KEY, LabelStatus, payload_key
from workload_graph.cost.snapshots import decimalise
from workload_graph.models import RawSnapshot, SourceName


class LabelledRun:
    """Runs labelled calls on one recorder and keeps their status."""

    def __init__(self, recorder: CallRecorder) -> None:
        self.recorder = recorder
        self.labels: list[dict[str, Any]] = []
        self.stopped = False

    def status_of(self, label: str) -> str | None:
        """Status recorded for ``label``, if it ran."""
        for entry in self.labels:
            if entry["label"] == label:
                return str(entry["status"])
        return None

    def skip(self, operation: str, label: str, reason: str) -> None:
        """Record a call that was not made, with a note."""
        self.labels.append({"label": label, "operation": operation, "status": "skipped",
                            "code": ""})
        self.recorder.notes.append(f"{payload_key(operation, label)}: skipped ({reason})")

    def pages(self, operation: str, label: str) -> list[dict[str, Any]]:
        """Pages recorded for a label so far."""
        return self.recorder.payload.get(payload_key(operation, label), [])

    def record(self, operation: str, label: str, page: dict[str, Any]) -> None:
        """Store a page with every float converted to ``Decimal``."""
        self.recorder.payload.setdefault(payload_key(operation, label), []).append(
            decimalise(page))

    def run(self, operation: str, label: str, fn: Callable[[], None]) -> LabelStatus:
        """Run one labelled call. After the budget runs out, later labels are skipped."""
        if self.stopped:
            self.skip(operation, label, "call budget exhausted")
            return "skipped"
        status: LabelStatus = "ok"
        code = ""
        recorder = self.recorder
        try:
            fn()
            recorder.outcomes.append("ok")
        except BudgetExceededError:
            recorder.budget_exhausted = True
            recorder.notes.append(f"call budget of {recorder.scope.max_calls} calls exhausted "
                                  f"during {label}")
            self.stopped = True
            status = "partial" if self.pages(operation, label) else "error"
            code = "BudgetExceeded"
        except NotEnabledError as exc:
            recorder.outcomes.append("not_enabled")
            recorder.notes.append(f"{exc.operation}:{label}: not enabled ({exc.code})")
            status, code = "not_enabled", exc.code
        except OperationDeniedError as exc:
            recorder.outcomes.append("denied")
            recorder.notes.append(f"{exc.operation}:{label}: access denied ({exc.code})")
            status, code = "denied", exc.code
        except (ThrottledOutError, OperationFailedError) as exc:
            recorder.outcomes.append("error")
            recorder.notes.append(f"{exc.operation}:{label}: failed ({exc.code})")
            status = "partial" if self.pages(operation, label) else "error"
            code = exc.code
        self.labels.append({"label": label, "operation": operation, "status": status,
                            "code": code})
        return status


def cost_snapshot(source: SourceName, account_id: str, collected_at: datetime,
                  run: LabelledRun, snapshot_id: str) -> RawSnapshot:
    """Assemble the snapshot from the recorder and the label records."""
    recorder = run.recorder
    payload = dict(recorder.payload)
    payload[LABELS_KEY] = list(run.labels)
    draft = RawSnapshot(
        snapshot_id=snapshot_id, source=source, account_id=account_id, region="us-east-1",
        collected_at=collected_at, operations_called=recorder.operations_called,
        payload=payload, status="ok", notes=recorder.notes)
    return draft.model_copy(update={"status": recorder.status(draft.has_data())})
