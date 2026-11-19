"""Declared groupings to seed clusters.

Status: real
Purpose: every stack group and every (tag key, value) group is a declaration; declarations
    that share a resource are merged with union-find. The connected groups are the seeds.
Inputs: the store (``STACK_MEMBER`` and ``TAG_WORKLOAD_KEY`` edges), excluded key strings
    (resources with a tag conflict).
Outputs: ``Seed`` list sorted by ``seed_id``.
Planned dependencies: none.
Notes:
    - Only grouping declarations merge seeds; no other edge type can (shared infrastructure
      must never collapse two workloads into one).
    - A merge of disagreeing declarations is not an error; it is recorded in ``declarations``.
    - Edge endpoints that are not stored resources connect groups but are not members.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from workload_graph.ids import ResourceKey
from workload_graph.models import GROUPING_EDGE_TYPES, EdgeType
from workload_graph.normalisers.tags import TAG_DETAIL_PREFIX
from workload_graph.store.base import GraphStore


def seed_id_for(members: Iterable[ResourceKey]) -> str:
    """First 12 hex chars of SHA-256 over the sorted member key strings."""
    text = "\n".join(sorted(k.key_str() for k in members))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Seed:
    """A cluster of declared members."""

    seed_id: str
    members: tuple[ResourceKey, ...]
    declarations: tuple[str, ...]

    @property
    def merged(self) -> bool:
        """True if several declarations were merged into this seed."""
        return len(self.declarations) > 1


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Smaller key string becomes the root, so the result never depends on order.
            lo, hi = sorted((ra, rb))
            self.parent[hi] = lo


def _declaration_label(store: GraphStore, edge_type: EdgeType, dst: ResourceKey,
                       detail: str) -> str:
    if edge_type is EdgeType.STACK_MEMBER:
        stack = store.get_resource(dst)
        name = (stack.name or stack.attributes.get("stack_name")) if stack else None
        return f"stack:{name or dst.arn}"
    return "tag:" + detail.removeprefix(TAG_DETAIL_PREFIX)


def build_seeds(store: GraphStore, excluded: set[str] | None = None) -> list[Seed]:
    """Group resources by grouping declarations; returns seeds sorted by id."""
    skip = excluded or set()
    uf = _UnionFind()
    labels: dict[str, set[str]] = {}
    keys: dict[str, ResourceKey] = {}
    for edge in store.edges():
        if edge.edge_type not in GROUPING_EDGE_TYPES or not isinstance(edge.dst, ResourceKey):
            continue
        src, dst = edge.src.key_str(), edge.dst.key_str()
        if src in skip or dst in skip:
            continue
        keys[src], keys[dst] = edge.src, edge.dst
        uf.union(src, dst)
        label = _declaration_label(store, edge.edge_type, edge.dst, edge.evidence[0].detail)
        labels.setdefault(src, set()).add(label)

    components: dict[str, list[str]] = {}
    for node in sorted(keys):
        components.setdefault(uf.find(node), []).append(node)

    seeds: list[Seed] = []
    for nodes in components.values():
        members = tuple(keys[n] for n in nodes if store.has_resource(keys[n]))
        if not members:
            continue
        declarations = sorted({lbl for n in nodes for lbl in labels.get(n, set())})
        seeds.append(Seed(seed_id=seed_id_for(members), members=members,
                          declarations=tuple(declarations)))
    return sorted(seeds, key=lambda s: s.seed_id)
