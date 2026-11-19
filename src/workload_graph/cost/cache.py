"""Snapshot cache for cost collections (F-D16).

Status: real
Purpose: avoid repeating paid Cost Explorer requests within a short time.
Inputs: a cache directory, an injected clock, a TTL in hours.
Outputs: ``SnapshotCache`` with ``key``, ``get``, ``put`` and ``fetch``.
Planned dependencies: cost.snapshots.
Notes:
    - The key is a hash of (source, access account, call label, parameters, ``as_of``).
    - A snapshot with status ``error`` is never stored.
    - A corrupt cache file is ignored with a note and the collector is called.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from workload_graph.cost.snapshots import dump_cost_snapshot
from workload_graph.models import RawSnapshot
from workload_graph.runstore import write_atomic


class SnapshotCache:
    """File cache of cost snapshots with a TTL measured on an injected clock."""

    def __init__(self, root: Path, clock: Callable[[], datetime], ttl_hours: int = 24) -> None:
        self.root = Path(root)
        self._clock = clock
        self._ttl = timedelta(hours=ttl_hours)

    @staticmethod
    def key(source: str, access_account_id: str, label: str, params: Mapping[str, Any],
            as_of: date) -> str:
        """Stable cache key; changes when any part changes."""
        canonical = json.dumps(
            {"source": source, "access": access_account_id, "label": label,
             "params": params, "as_of": as_of.isoformat()},
            sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> tuple[RawSnapshot | None, list[str]]:
        """The cached snapshot if it is younger than the TTL, plus notes."""
        path = self._path(key)
        if not path.is_file():
            return None, []
        try:
            data = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
            stored_at = datetime.fromisoformat(str(data["stored_at"]))
            snapshot = RawSnapshot.model_validate(data["snapshot"])
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, ValidationError):
            return None, [f"cache entry {key[:12]} is unreadable; ignored"]
        if self._clock() - stored_at >= self._ttl:
            return None, []
        return snapshot, []

    def put(self, key: str, snapshot: RawSnapshot) -> bool:
        """Store a snapshot. Returns False (and stores nothing) for status ``error``."""
        if snapshot.status == "error":
            return False
        body = json.loads(dump_cost_snapshot(snapshot))
        text = json.dumps({"stored_at": self._clock().isoformat(), "snapshot": body},
                          sort_keys=True, indent=2) + "\n"
        write_atomic(self._path(key), text)
        return True

    def fetch(self, key: str,
              collect: Callable[[], RawSnapshot]) -> tuple[RawSnapshot, list[str], bool]:
        """Cached snapshot, or a fresh one from ``collect`` (then stored). Third item: hit."""
        cached, notes = self.get(key)
        if cached is not None:
            return cached, notes, True
        fresh = collect()
        self.put(key, fresh)
        return fresh, notes, False
