"""Offline pipeline: probe, normalise, store, build, report.

Status: real
Purpose: run the builder over stored snapshots, with no AWS and no network access.
Inputs: a snapshot directory, a ruleset and a workspace.
Outputs: ``BuildResult``.
Planned dependencies: probe, normalisers, store, graph builder.
Notes: only ``<account-id>/<source>.json`` files are loaded. Anything else is reported
    in ``notes`` and skipped. An unreadable snapshot is a degradation, not a failure.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from workload_graph.graph.build import build_workloads
from workload_graph.models import COST_SOURCES, SOURCES, BuildResult, RawSnapshot, Workspace
from workload_graph.normalisers import normalise
from workload_graph.probe import probe_sources
from workload_graph.ruleset import Ruleset
from workload_graph.store.memory import InMemoryGraphStore

_ACCOUNT_DIR = re.compile(r"^[0-9]{12}$")
_SOURCES = set(SOURCES)
_COST_SOURCES = set(COST_SOURCES)


def load_snapshots(snapshots_dir: Path) -> tuple[list[RawSnapshot], list[str]]:
    """Load snapshot files. Unreadable files and unexpected paths become notes."""
    root = Path(snapshots_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"snapshot directory not found: {root}")
    snapshots: list[RawSnapshot] = []
    notes: list[str] = []
    for account_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if _ACCOUNT_DIR.fullmatch(account_dir.name) is None:
            notes.append(f"ignored path {account_dir.name}: not an account directory")
            continue
        for path in sorted(account_dir.glob("*.json")):
            relative = f"{account_dir.name}/{path.name}"
            if path.stem in _COST_SOURCES:
                continue  # read by the findings pipeline, not the graph build
            if path.stem not in _SOURCES:
                notes.append(f"ignored {relative}: not a known source")
                continue
            try:
                snapshots.append(RawSnapshot.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, ValidationError):
                notes.append(f"unreadable snapshot {relative}: {path.name} was skipped")
    return snapshots, notes


def run_snapshots(snapshots: Sequence[RawSnapshot], ruleset: Ruleset, workspace: Workspace,
                  notes: Sequence[str] = ()) -> BuildResult:
    """Probe, normalise, store and build from snapshots already in memory."""
    probe = probe_sources(snapshots, workspace.approved_account_ids)
    store = InMemoryGraphStore()
    for snapshot in snapshots:
        store.add_batch(normalise(snapshot, ruleset))
    return build_workloads(
        store, ruleset, workspace, probe,
        [snapshot.snapshot_id for snapshot in snapshots], notes=notes,
    )


def run(snapshots_dir: Path, ruleset: Ruleset, workspace: Workspace) -> BuildResult:
    """Load snapshots, probe, normalise, store and build."""
    snapshots, notes = load_snapshots(snapshots_dir)
    return run_snapshots(snapshots, ruleset, workspace, notes)
