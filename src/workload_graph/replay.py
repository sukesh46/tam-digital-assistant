"""Offline replay of a saved run: no live AWS calls.

Status: real (local workaround)
Purpose: when DNS or the network blocks live collectors, the UI can still show graphs and the
    ledger by replaying snapshots already saved under ``out/runs/<id>/``.
Inputs: a run directory with ``graph/<account>/<source>.json`` and ``cost/<account>/<source>.json``.
Outputs: ``ReplayPlanner``, ``replay_cost_collect``.
Notes:
    - No STS, no AssumeRole, no network. Progress events still fire so the page looks alive.
    - Temporary: remove once the machine's DNS answers AWS hosts reliably.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from workload_graph.cost.models import CostScope
from workload_graph.cost.snapshots import load_cost_snapshot
from workload_graph.credentials import ClientFactory, validate_region
from workload_graph.fanout import Collector
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.ids import validate_account_id
from workload_graph.live import Progress
from workload_graph.models import COST_SOURCES, RawSnapshot
from workload_graph.pipeline import load_snapshots


def _graph_dir(run_dir: Path) -> Path:
    nested = run_dir / "graph"
    return nested if nested.is_dir() else run_dir


def _cost_dir(run_dir: Path) -> Path:
    return run_dir / "cost"


class ReplayPlanner:
    """Serves saved graph snapshots for the accounts present under the run directory."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = Path(run_dir)
        graph = _graph_dir(self._run_dir)
        if not graph.is_dir():
            raise ValueError(f"replay directory has no graph snapshots: {run_dir}")
        self._graph = graph

    def prepare(self, accounts: tuple[str, ...], region: str) -> Collector:
        validate_region(region)
        wanted = {validate_account_id(a) for a in accounts}
        available, _notes = load_snapshots(self._graph)
        by_account: dict[str, list[RawSnapshot]] = {}
        for snapshot in available:
            by_account.setdefault(snapshot.account_id, []).append(snapshot)
        missing = sorted(wanted - set(by_account))
        if missing:
            raise ValueError(
                "replay has no graph snapshots for "
                + ", ".join(missing)
                + "; known accounts: "
                + (", ".join(sorted(by_account)) or "none"),
            )

        def collect(account_id: str, progress: Progress) -> list[RawSnapshot]:
            snapshots = list(by_account[account_id])
            for snapshot in snapshots:
                progress("source_started", snapshot.source)
                progress("source_done", f"{snapshot.source}:{snapshot.status}")
            return snapshots

        return collect

    def cost_factory(self, accounts: tuple[str, ...]) -> ClientFactory:
        for account_id in accounts:
            validate_account_id(account_id)
        return lambda *_a: None


def replay_cost_collect(run_dir: Path):
    """A ``cost_collect`` that reads saved cost snapshots instead of calling AWS."""
    root = _cost_dir(Path(run_dir))

    def collect(_factory: ClientFactory, account_id: str, _scope: CostScope,
                _rules: FindingsRuleset, _at: datetime, progress: Progress) -> list[RawSnapshot]:
        account_dir = root / validate_account_id(account_id)
        if not account_dir.is_dir():
            raise FileNotFoundError(f"no saved cost snapshots for {account_id}")
        snapshots: list[RawSnapshot] = []
        for source in COST_SOURCES:
            path = account_dir / f"{source}.json"
            if not path.is_file():
                continue
            progress("cost_source_started", source)
            snapshot = load_cost_snapshot(path)
            progress("cost_source_done", f"{source}:{snapshot.status}")
            snapshots.append(snapshot)
        if not snapshots:
            raise FileNotFoundError(f"no saved cost snapshots for {account_id}")
        return snapshots

    return collect
