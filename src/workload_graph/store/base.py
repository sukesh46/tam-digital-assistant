"""GraphStore protocol.

Status: real
Purpose: storage contract for resources and edges during one build run.
Inputs: resources, edges, normalised batches.
Outputs: sorted exports and neighbour queries.
Planned dependencies: none.
Notes: stores are append-only for the duration of a build; there is no removal API.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from workload_graph.ids import ResourceKey
from workload_graph.models import Edge, Resource
from workload_graph.normalisers.base import NormalisedBatch


@runtime_checkable
class GraphStore(Protocol):
    """Append-only store of resources and edges with deterministic, sorted reads."""

    def add_resource(self, resource: Resource) -> None:
        """Add a resource, merging with any existing record for the same key."""
        ...

    def add_edge(self, edge: Edge) -> None:
        """Add an edge, merging evidence with any edge of the same identity."""
        ...

    def add_batch(self, batch: NormalisedBatch) -> None:
        """Add every resource and edge of a normalised batch."""
        ...

    def get_resource(self, key: ResourceKey) -> Resource | None:
        """Return the resource for ``key``, if present."""
        ...

    def has_resource(self, key: ResourceKey) -> bool:
        """True if a resource with ``key`` is present."""
        ...

    def resources(self) -> list[Resource]:
        """All resources, sorted by key."""
        ...

    def edges(self) -> list[Edge]:
        """All edges, sorted by identity."""
        ...

    def edges_of(self, key: ResourceKey) -> list[Edge]:
        """Edges with ``key`` as source or destination, sorted by identity."""
        ...

    def neighbours(self, key: ResourceKey) -> list[ResourceKey]:
        """Resource keys linked to ``key`` in either direction (not itself), sorted."""
        ...

    def accounts(self) -> list[str]:
        """Accounts of the stored resources, sorted."""
        ...
