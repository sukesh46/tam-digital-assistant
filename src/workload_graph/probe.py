"""Capability probe from snapshot statuses.

Status: real
Purpose: map snapshot statuses to ``SourceAvailability`` per account and source, so the
    output shows which sources could contribute.
Inputs: snapshots, the accounts in scope, the sources to report.
Outputs: ``ProbeResult`` with entries sorted by (account, source).
Planned dependencies: none.
Notes: several regions for one account and source are combined: identical statuses are kept;
    a mix that includes data is ``partial``; otherwise denied > error > empty.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from workload_graph.models import (
    SOURCES,
    ProbeEntry,
    ProbeResult,
    RawSnapshot,
    SourceAvailability,
    SourceName,
)

_NO_DATA_PRIORITY: tuple[SourceAvailability, ...] = ("denied", "error", "empty")


def snapshot_availability(snapshot: RawSnapshot) -> SourceAvailability:
    """Availability implied by one snapshot."""
    if snapshot.status == "ok":
        return "available" if snapshot.has_data() else "empty"
    return snapshot.status


def _combine(values: list[SourceAvailability]) -> SourceAvailability:
    distinct = set(values)
    if len(distinct) == 1:
        return values[0]
    if distinct & {"available", "partial"}:
        return "partial"
    for candidate in _NO_DATA_PRIORITY:
        if candidate in distinct:
            return candidate
    return "error"  # pragma: no cover


def probe_sources(
    snapshots: Iterable[RawSnapshot],
    account_ids: Sequence[str],
    sources: Sequence[SourceName] = SOURCES,
) -> ProbeResult:
    """Build a ``ProbeResult`` for every (account, source) pair in scope."""
    accounts = sorted(set(account_ids))
    wanted = sorted(set(sources))
    by_pair: dict[tuple[str, str], list[RawSnapshot]] = {}
    notes: set[str] = set()
    for snap in snapshots:
        if snap.account_id not in accounts:
            notes.add(f"snapshot {snap.snapshot_id} is for account {snap.account_id}, "
                      "which is not in scope; ignored")
            continue
        by_pair.setdefault((snap.account_id, snap.source), []).append(snap)

    entries: list[ProbeEntry] = []
    for account in accounts:
        for source in wanted:
            found = sorted(by_pair.get((account, source), []),
                           key=lambda s: (s.region, s.snapshot_id))
            if not found:
                entries.append(ProbeEntry(account_id=account, source=source,
                                          availability="error",
                                          notes=[f"no snapshot for {source} in {account}"]))
                continue
            per_region = [snapshot_availability(s) for s in found]
            entry_notes: list[str] = []
            if len(set(per_region)) > 1:
                detail = ", ".join(f"{s.region}={a}" for s, a in zip(found, per_region,
                                                                    strict=True))
                entry_notes.append(f"regions differ: {detail}")
            entries.append(ProbeEntry(account_id=account, source=source,
                                      availability=_combine(per_region), notes=entry_notes))
    return ProbeResult(entries=entries, notes=sorted(notes))
