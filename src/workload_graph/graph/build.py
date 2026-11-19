"""Build orchestration: seeds, shared, attach, residual, confidence, naming, cross-account.

Status: real
Purpose: ``build_workloads()`` turns a populated store into a ``BuildResult``.
Inputs: the store, ruleset, workspace (approved scope), probe result, snapshot ids, notes.
Outputs: ``BuildResult`` with every list in stable sorted order.
Planned dependencies: graph modules.
Notes:
    - Every stored resource ends up in exactly one of: a workload's members, shared
      infrastructure, a residual group, or unmapped.
    - Every degradation (non-available probe entries, pending or external cross-account
      references, merged declarations, ambiguous resources) is written to ``notes``.
"""

from __future__ import annotations

from collections.abc import Sequence

from workload_graph.graph.attach import MEMBER_EVIDENCE_CAP, Attachment, attach
from workload_graph.graph.confidence import derive_confidence
from workload_graph.graph.crossaccount import CrossAccountReport, resolve_cross_account
from workload_graph.graph.naming import name_hint
from workload_graph.graph.residual import build_residuals
from workload_graph.graph.seeds import Seed, build_seeds
from workload_graph.graph.shared import find_shared
from workload_graph.ids import ResourceKey
from workload_graph.models import (
    GROUPING_EDGE_TYPES,
    BuildResult,
    CandidateWorkload,
    EdgeType,
    Evidence,
    Member,
    ProbeResult,
    SharedInfra,
    Unplaced,
    WorkloadEdge,
    Workspace,
)
from workload_graph.ruleset import Ruleset
from workload_graph.store.base import GraphStore


def _declared_member(store: GraphStore, key: ResourceKey, members: set[ResourceKey]) -> Member:
    reasons: set[EdgeType] = set()
    evidence: set[Evidence] = set()
    for edge in store.edges_of(key):
        if edge.edge_type not in GROUPING_EDGE_TYPES or not isinstance(edge.dst, ResourceKey):
            continue
        other = edge.dst if edge.src == key else edge.src
        if other in members:
            reasons.add(edge.edge_type)
            evidence.update(edge.evidence)
    ordered = sorted(evidence, key=Evidence.sort_key)[:MEMBER_EVIDENCE_CAP]
    return Member(key=key, membership="declared", affinity=None, reasons=sorted(reasons),
                  evidence=ordered)


def workload_edges(store: GraphStore, members: set[ResourceKey]) -> list[WorkloadEdge]:
    """Edges whose source is a member, in a stable order.

    The list depends only on the store and the member set: edges are filtered, then sorted by
    source, destination and type. Input order cannot change it.
    """
    found: list[WorkloadEdge] = []
    for edge in store.edges():
        if edge.src not in members:
            continue
        evidence = sorted(edge.evidence, key=Evidence.sort_key)[:MEMBER_EVIDENCE_CAP]
        found.append(WorkloadEdge(src=edge.src, dst=edge.dst_str(), edge_type=edge.edge_type,
                                  evidence=evidence))
    found.sort(key=lambda item: (item.src.key_str(), item.dst, item.edge_type.value))
    return found


def _evidence_summary(store: GraphStore, members: set[ResourceKey]) -> dict[str, int]:
    seen: set[tuple[str, str, str]] = set()
    counts: dict[str, int] = {}
    for key in sorted(members):
        for edge in store.edges_of(key):
            identity = edge.identity()
            if identity in seen:
                continue
            if edge.edge_type in GROUPING_EDGE_TYPES and not (
                    edge.src in members and edge.dst in members):
                continue
            seen.add(identity)
            counts[edge.edge_type.value] = counts.get(edge.edge_type.value, 0) + 1
    return dict(sorted(counts.items()))


def _workload(store: GraphStore, seed: Seed, attached: list[Attachment],
              shared: dict[ResourceKey, list[str]], cross: CrossAccountReport,
              ruleset: Ruleset) -> CandidateWorkload:
    declared_keys = set(seed.members)
    all_keys = declared_keys | {a.key for a in attached}
    members = [_declared_member(store, k, declared_keys) for k in seed.members]
    members += [Member(key=a.key, membership="attached", affinity=a.affinity,
                       reasons=list(a.reasons), evidence=list(a.evidence)) for a in attached]
    members.sort(key=lambda m: m.key)
    resources = [r for r in (store.get_resource(k) for k in sorted(all_keys)) if r is not None]
    sources = sorted({s for r in resources for s in r.sources})
    deps, externals = cross.for_members(all_keys)
    shared_deps = {k for k, touches in shared.items() if seed.seed_id in touches}
    return CandidateWorkload(
        workload_id=seed.seed_id,
        name_hint=name_hint(resources, ruleset, seed.seed_id),
        members=members,
        confidence=derive_confidence(len(declared_keys), len(all_keys), sources, ruleset),
        accounts=sorted({k.account_id for k in all_keys}),
        sources_used=sources,
        shared_dependencies=sorted(shared_deps | set(deps)),
        external_refs=externals,
        evidence_summary=_evidence_summary(store, all_keys),
        edges=workload_edges(store, all_keys),
    )


def build_workloads(
    store: GraphStore,
    ruleset: Ruleset,
    scope: Workspace,
    probe: ProbeResult,
    snapshot_ids: Sequence[str],
    notes: Sequence[str] = (),
) -> BuildResult:
    """Run seeds, shared detection, attachment, residuals and cross-account resolution."""
    excluded = {r.key.key_str() for r in store.resources()
                if r.attributes.get("tag_conflict") == "true"}
    seeds = build_seeds(store, excluded)
    shared = find_shared(store, seeds, ruleset, excluded)
    attachments, rejections = attach(store, seeds, set(shared), excluded, ruleset)
    cross = resolve_cross_account(store, scope.approved_account_ids,
                                  ruleset.known_aws_service_account_ids)

    by_seed: dict[str, list[Attachment]] = {}
    for item in attachments:
        by_seed.setdefault(item.seed_id, []).append(item)
    workloads = [_workload(store, s, by_seed.get(s.seed_id, []), shared, cross, ruleset)
                 for s in seeds]

    unplaced = [Unplaced(key=r.key, reason=r.reason, candidates=list(r.candidates),
                         best_affinity=r.best_affinity) for r in rejections]
    unplaced += [Unplaced(key=r.key, reason="tag_conflict") for r in store.resources()
                 if r.key.key_str() in excluded]
    residual, unmapped = build_residuals(unplaced, store, set(shared))

    all_notes = set(notes) | set(probe.notes) | set(cross.notes)
    all_notes.update(f"{e.source} {e.availability} in {e.account_id}"
                     for e in probe.entries if e.availability != "available")
    all_notes.update(f"seed {s.seed_id} merges declarations: {', '.join(s.declarations)}"
                     for s in seeds if s.merged)
    all_notes.update(f"ambiguous: {r.key.key_str()} links equally to {', '.join(r.candidates)}"
                     for r in rejections if r.reason == "ambiguous")

    return BuildResult(
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash(),
        workloads=workloads,
        shared_infrastructure=[SharedInfra(key=k, touches=v) for k, v in shared.items()],
        residual_groups=residual,
        unmapped=unmapped,
        external_refs=list(cross.summaries),
        pending_refs=list(cross.pending),
        probe=probe,
        notes=sorted(all_notes),
        generated_from=sorted(set(snapshot_ids)),
    )
