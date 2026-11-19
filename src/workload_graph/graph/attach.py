"""Affinity scoring and attachment.

Status: real
Purpose: attach each undeclared resource to the seed it is most strongly linked to, if the
    link is strong and unambiguous enough; otherwise record why not.
Inputs: the store, seeds, shared hub keys, excluded key strings, ruleset.
Outputs: ``Attachment`` and ``Rejection`` lists sorted by key.
Planned dependencies: none.
Notes:
    - Affinity = 1 - prod(1 - w) over the DISTINCT edge types linking the resource to the
      cluster (bounded, member-count independent, order independent, explainable).
    - A resource is linked to a cluster by edge type t if it has a direct edge of type t to or
      from a member, or if it and a member both point to the same container with type t.
    - Single pass against seed members only; attached resources do not attract others.
    - Attach iff best >= attach_threshold and best - second >= attach_margin. Ties rank by
      lowest seed id.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from workload_graph.graph.seeds import Seed
from workload_graph.graph.shared import seed_index
from workload_graph.ids import ResourceKey
from workload_graph.models import (
    CONTAINER_EDGE_TYPES,
    GROUPING_EDGE_TYPES,
    EdgeType,
    Evidence,
    UnplacedReason,
)
from workload_graph.ruleset import Ruleset
from workload_graph.store.base import GraphStore

# SKELETON: cap on evidence records kept per member, to bound output size.
MEMBER_EVIDENCE_CAP = 10
_PRECISION = 6


def affinity(weights: Iterable[float]) -> float:
    """``1 - prod(1 - w)``, computed over sorted weights and rounded to 6 places."""
    remaining = 1.0
    for weight in sorted(weights):
        remaining *= 1.0 - weight
    return round(1.0 - remaining, _PRECISION)


@dataclass(frozen=True)
class Attachment:
    """An undeclared resource attached to a seed."""

    key: ResourceKey
    seed_id: str
    affinity: float
    reasons: tuple[EdgeType, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Rejection:
    """An undeclared resource that was not attached, and why."""

    key: ResourceKey
    reason: UnplacedReason
    candidates: tuple[str, ...]
    best_affinity: float | None


def _container_index(store: GraphStore, seeds: list[Seed]
                     ) -> dict[tuple[str, EdgeType], dict[str, Evidence]]:
    """``(container, type) -> {seed id: one member's evidence}``."""
    index: dict[tuple[str, EdgeType], dict[str, Evidence]] = {}
    for seed in seeds:
        for member in seed.members:
            for edge in store.edges_of(member):
                if (edge.edge_type in CONTAINER_EDGE_TYPES and edge.src == member
                        and isinstance(edge.dst, ResourceKey)):
                    slot = index.setdefault((edge.dst.key_str(), edge.edge_type), {})
                    first = min(edge.evidence, key=Evidence.sort_key)
                    if seed.seed_id not in slot or first.sort_key() < slot[seed.seed_id].sort_key():
                        slot[seed.seed_id] = first
    return index


def attach(store: GraphStore, seeds: list[Seed], shared: set[ResourceKey],
           excluded: set[str], ruleset: Ruleset) -> tuple[list[Attachment], list[Rejection]]:
    """Attach candidates to seeds. Candidates are resources not seeded, shared or excluded."""
    member_of = seed_index(seeds)
    containers = _container_index(store, seeds)
    attachments: list[Attachment] = []
    rejections: list[Rejection] = []
    for resource in store.resources():
        key = resource.key
        ks = key.key_str()
        if ks in member_of or ks in excluded or key in shared:
            continue
        links: dict[str, dict[EdgeType, set[Evidence]]] = {}
        for edge in store.edges_of(key):
            edge_type = edge.edge_type
            if (edge_type in GROUPING_EDGE_TYPES or ruleset.weight(edge_type) <= 0.0
                    or not isinstance(edge.dst, ResourceKey) or edge.src == edge.dst):
                continue
            other = edge.dst if edge.src == key else edge.src
            seed = member_of.get(other.key_str())
            if seed is not None:
                links.setdefault(seed, {}).setdefault(edge_type, set()).update(edge.evidence)
            if edge_type in CONTAINER_EDGE_TYPES and edge.src == key:
                for seed_id, member_ev in containers.get((other.key_str(), edge_type),
                                                         {}).items():
                    bucket = links.setdefault(seed_id, {}).setdefault(edge_type, set())
                    bucket.update(edge.evidence)
                    bucket.add(member_ev)
        if not links:
            rejections.append(Rejection(key, "no_edges", (), None))
            continue
        scores = sorted(
            ((affinity(ruleset.weight(t) for t in types), seed_id)
             for seed_id, types in links.items()),
            key=lambda item: (-item[0], item[1]),
        )
        best_score, best_seed = scores[0]
        second = scores[1][0] if len(scores) > 1 else None
        margin_ok = (second is None
                     or round(best_score - second, _PRECISION) >= ruleset.attach_margin)
        if best_score >= ruleset.attach_threshold and margin_ok:
            types = links[best_seed]
            evidence = sorted({e for evs in types.values() for e in evs}, key=Evidence.sort_key)
            attachments.append(Attachment(
                key=key, seed_id=best_seed, affinity=best_score,
                reasons=tuple(sorted(types)), evidence=tuple(evidence[:MEMBER_EVIDENCE_CAP]),
            ))
        elif best_score >= ruleset.attach_threshold:
            close = tuple(sorted(s for score, s in scores
                                 if round(best_score - score, _PRECISION) < ruleset.attach_margin))
            rejections.append(Rejection(key, "ambiguous", close, best_score))
        else:
            rejections.append(Rejection(key, "below_threshold", (best_seed,), best_score))
    return attachments, rejections
