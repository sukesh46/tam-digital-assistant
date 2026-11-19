"""In-memory graph store backed by networkx.

Status: real
Purpose: append-only store for one build run, with neighbour queries via networkx.
Inputs: resources, edges, normalised batches.
Outputs: sorted resources, edges and neighbour lists.
Planned dependencies: networkx.
Notes: graph nodes are key strings; external endpoints use their ``sort_key`` and are never
    returned as neighbours. Every read is sorted so iteration order never leaks into output.
"""

from __future__ import annotations

import networkx as nx

from workload_graph.ids import ResourceKey
from workload_graph.models import Edge, Resource
from workload_graph.normalisers.base import NormalisedBatch


class InMemoryGraphStore:
    """networkx-backed implementation of ``GraphStore``."""

    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}
        self._keys: dict[str, ResourceKey] = {}
        self._incident: dict[str, set[tuple[str, str, str]]] = {}
        self._graph: nx.Graph[str] = nx.Graph()

    def add_resource(self, resource: Resource) -> None:
        """Add or merge a resource."""
        key = resource.key.key_str()
        existing = self._resources.get(key)
        self._resources[key] = existing.merged_with(resource) if existing else resource
        self._keys[key] = resource.key
        self._graph.add_node(key)

    def add_edge(self, edge: Edge) -> None:
        """Add an edge or merge its evidence into an existing one."""
        identity = edge.identity()
        existing = self._edges.get(identity)
        self._edges[identity] = existing.merged_with(edge) if existing else edge
        src, dst = identity[0], identity[1]
        self._keys[src] = edge.src
        if isinstance(edge.dst, ResourceKey):
            self._keys[dst] = edge.dst
        self._incident.setdefault(src, set()).add(identity)
        self._incident.setdefault(dst, set()).add(identity)
        if src != dst and isinstance(edge.dst, ResourceKey):
            self._graph.add_edge(src, dst)

    def add_batch(self, batch: NormalisedBatch) -> None:
        """Add every resource and edge in a batch."""
        for resource in batch.resources:
            self.add_resource(resource)
        for edge in batch.edges:
            self.add_edge(edge)

    def get_resource(self, key: ResourceKey) -> Resource | None:
        """Resource for ``key`` or None."""
        return self._resources.get(key.key_str())

    def has_resource(self, key: ResourceKey) -> bool:
        """True if ``key`` is a stored resource."""
        return key.key_str() in self._resources

    def resources(self) -> list[Resource]:
        """All resources sorted by key."""
        return [self._resources[k] for k in sorted(self._resources)]

    def edges(self) -> list[Edge]:
        """All edges sorted by identity."""
        return [self._edges[i] for i in sorted(self._edges)]

    def edges_of(self, key: ResourceKey) -> list[Edge]:
        """Incident edges sorted by identity."""
        return [self._edges[i] for i in sorted(self._incident.get(key.key_str(), set()))]

    def neighbours(self, key: ResourceKey) -> list[ResourceKey]:
        """Neighbouring resource keys, sorted."""
        node = key.key_str()
        if node not in self._graph:
            return []
        return [self._keys[n] for n in sorted(self._graph.neighbors(node))]

    def accounts(self) -> list[str]:
        """Sorted accounts of stored resources."""
        return sorted({r.key.account_id for r in self._resources.values()})
