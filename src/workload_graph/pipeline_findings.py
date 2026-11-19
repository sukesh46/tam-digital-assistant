"""Offline findings pipeline: build result plus cost snapshots to a findings report.

Status: real
Purpose: the cost layer's entry point, used by ``workload-graph findings`` and the live app.
Inputs: a ``BuildResult``, cost snapshots, optional graph snapshots (for tags), rulesets, as_of.
Outputs: ``FindingsReport``.
Planned dependencies: findings, report, normalisers.tags, pipeline.
Notes:
    - Only ``<account-id>/cost_explorer.json`` and ``cost_optimization_hub.json`` are read.
    - Tags come from the graph snapshots when given; without them tag attribution is
      unavailable, which the report says.
    - ``approved`` limits the workloads counted; members of others are treated as unplaced.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from workload_graph.cost.snapshots import load_cost_snapshot
from workload_graph.findings.build import build_findings
from workload_graph.findings.index import ResourceIndex
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import COST_SOURCES, BuildResult, RawSnapshot
from workload_graph.normalisers.tags import normalise_tags
from workload_graph.pipeline import load_snapshots
from workload_graph.report.assemble import assemble
from workload_graph.report.models import FindingsReport
from workload_graph.report.narrative import Narrator
from workload_graph.ruleset import Ruleset


def load_cost_snapshots(snapshots_dir: Path) -> tuple[list[RawSnapshot], list[str]]:
    """Cost snapshots under ``<account-id>/``. Unreadable files become notes."""
    root = Path(snapshots_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"snapshot directory not found: {root}")
    snapshots: list[RawSnapshot] = []
    notes: list[str] = []
    for account_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for source in COST_SOURCES:
            path = account_dir / f"{source}.json"
            if not path.is_file():
                continue
            try:
                snapshots.append(load_cost_snapshot(path))
            except (OSError, UnicodeError, ValueError, ValidationError):
                notes.append(f"unreadable snapshot {account_dir.name}/{path.name}: skipped")
    return snapshots, notes


def tag_map(snapshots: Sequence[RawSnapshot], ruleset: Ruleset) -> dict[str, dict[str, str]]:
    """Resource key to tags, from the tags snapshots of a graph collection."""
    tags: dict[str, dict[str, str]] = {}
    for snapshot in snapshots:
        if snapshot.source != "tags":
            continue
        for resource in normalise_tags(snapshot, ruleset).resources:
            tags[resource.key.key_str()] = dict(resource.tags)
    return tags


def run(result: BuildResult, cost_snapshots: Sequence[RawSnapshot], *, as_of: date,
        findings_ruleset: FindingsRuleset | None = None, workload_ruleset: Ruleset | None = None,
        graph_snapshots: Sequence[RawSnapshot] | None = None, approved: set[str] | None = None,
        narrator: Narrator | None = None, notes: Sequence[str] = ()) -> FindingsReport:
    """Build the findings report from data already in memory."""
    rules = findings_ruleset or FindingsRuleset()
    workload_rules = workload_ruleset or Ruleset()
    extra = list(notes)
    tags = None
    if graph_snapshots is None:
        extra.append("graph snapshots not given: tag attribution unavailable")
    else:
        tags = tag_map(graph_snapshots, workload_rules)
    index = ResourceIndex(result, tags=tags, approved=approved)
    ce = [s for s in cost_snapshots if s.source == "cost_explorer"]
    coh = [s for s in cost_snapshots if s.source == "cost_optimization_hub"]
    access = sorted({s.account_id for s in cost_snapshots})
    linked = access or sorted({a for w in index.workloads.values() for a in w.accounts})
    bundle = build_findings(ce, coh, index, rules, workload_rules.workload_tag_keys, linked,
                            as_of)
    return assemble(bundle, result, ruleset=rules, snapshots=cost_snapshots,
                    access_accounts=access or linked, linked_accounts=linked, as_of=as_of,
                    narrator=narrator, approved=approved, notes=extra)


def run_paths(build_result: Path, snapshots_dir: Path, as_of: date,
              findings_ruleset: Path | None = None,
              graph_snapshots: Path | None = None) -> FindingsReport:
    """Build the findings report from files."""
    result = BuildResult.model_validate_json(build_result.read_text(encoding="utf-8"))
    rules = FindingsRuleset.load(findings_ruleset) if findings_ruleset else FindingsRuleset()
    cost, notes = load_cost_snapshots(snapshots_dir)
    graph = None
    if graph_snapshots is not None:
        graph, graph_notes = load_snapshots(graph_snapshots)
        notes += graph_notes
    return run(result, cost, as_of=as_of, findings_ruleset=rules, graph_snapshots=graph,
               notes=notes)
