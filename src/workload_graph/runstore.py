"""Run persistence: one directory per run under ``out/runs/``.

Status: real
Purpose: keep runs, their build result, the event log and the TAM's decisions across restarts,
    so a review can be finished later and the cost review can read approved runs.
Inputs: ``Job`` objects; a root directory (``out/runs`` by default, gitignored).
Outputs: ``RunStore`` with ``save``, ``load_all`` and ``run_dir``.
Planned dependencies: jobs, models.
Notes:
    - ``out/`` is in ``.gitignore``. Account ids written here never enter the repository.
    - Writes go to a temporary file in the same directory and are then renamed, so a reader
      never sees a half-written file.
    - An unreadable run file is skipped, never fatal.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from workload_graph.jobs import Job, JobEvent, JobState
from workload_graph.models import BuildResult

SCHEMA_VERSION = "1"
RUN_FILE = "run.json"
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temporary file and a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _event_dict(event: JobEvent) -> dict[str, Any]:
    return {"seq": event.seq, "at": event.at.isoformat(), "kind": event.kind,
            "account_id": event.account_id, "detail": event.detail}


def _event(data: dict[str, Any]) -> JobEvent:
    return JobEvent(int(data["seq"]), datetime.fromisoformat(str(data["at"])), str(data["kind"]),
                    None if data.get("account_id") is None else str(data["account_id"]),
                    str(data.get("detail", "")))


class RunStore:
    """Saves and loads runs. Thread-safe: one lock around every write."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def run_dir(self, job_id: str) -> Path:
        """Directory of one run. Refuses anything that is not a run id."""
        if _RUN_ID.fullmatch(job_id) is None:
            raise ValueError("not a run id")
        return self.root / job_id

    def save(self, job: Job) -> None:
        """Write the run's current state."""
        result = job.result
        body = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job.job_id,
            "accounts": list(job.accounts),
            "region": job.region,
            "created_at": job.created_at.isoformat(),
            "state": job.state.value,
            "events": [_event_dict(e) for e in job.events_after(0)],
            "decisions": job.decisions,
            "result": None if result is None else json.loads(result.to_json()),
        }
        text = json.dumps(body, sort_keys=True, indent=2) + "\n"
        with self._lock:
            write_atomic(self.run_dir(job.job_id) / RUN_FILE, text)

    def load_all(self) -> list[Job]:
        """Every readable run under the root, oldest first."""
        if not self.root.is_dir():
            return []
        jobs: list[Job] = []
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or _RUN_ID.fullmatch(folder.name) is None:
                continue
            job = self._load(folder / RUN_FILE)
            if job is not None:
                jobs.append(job)
        return sorted(jobs, key=lambda j: (j.created_at, j.job_id))

    def _load(self, path: Path) -> Job | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result = data.get("result")
            return Job.restore(
                job_id=str(data["job_id"]),
                accounts=tuple(str(a) for a in data["accounts"]),
                region=str(data.get("region", "")),
                created_at=datetime.fromisoformat(str(data["created_at"])),
                state=JobState(str(data["state"])),
                events=[_event(e) for e in data.get("events", [])],
                result=None if result is None else BuildResult.model_validate(result),
                decisions={str(k): str(v) for k, v in dict(data.get("decisions", {})).items()},
            )
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, ValidationError):
            return None
