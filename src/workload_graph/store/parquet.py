"""Optional Parquet graph store.

Status: real
Purpose: persist resources and edges as Parquet and serve the ``GraphStore`` contract.
Inputs: a directory path, resources and edges.
Outputs: the same sorted reads as the in-memory store, including after reopening the directory.
Planned dependencies: pyarrow (optional extra, D14).
Notes: reads are served from an in-memory copy loaded from the Parquet files. The core
    package does not depend on pyarrow.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from workload_graph.ids import ResourceKey
from workload_graph.models import Edge, Resource
from workload_graph.normalisers.base import NormalisedBatch
from workload_graph.store.memory import InMemoryGraphStore


class ParquetGraphStore:
    """``GraphStore`` saved as ``resources.parquet`` and ``edges.parquet``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._inner = InMemoryGraphStore()
        self._load()

    def add_resource(self, resource: Resource) -> None:
        """Add or merge a resource and write it to Parquet."""
        self._inner.add_resource(resource)
        self._flush()

    def add_edge(self, edge: Edge) -> None:
        """Add or merge an edge and write it to Parquet."""
        self._inner.add_edge(edge)
        self._flush()

    def add_batch(self, batch: NormalisedBatch) -> None:
        """Add a batch and write the store to Parquet once."""
        self._inner.add_batch(batch)
        self._flush()

    def get_resource(self, key: ResourceKey) -> Resource | None:
        """Resource for ``key`` or None."""
        return self._inner.get_resource(key)

    def has_resource(self, key: ResourceKey) -> bool:
        """True if ``key`` is stored."""
        return self._inner.has_resource(key)

    def resources(self) -> list[Resource]:
        """All resources sorted by key."""
        return self._inner.resources()

    def edges(self) -> list[Edge]:
        """All edges sorted by identity."""
        return self._inner.edges()

    def edges_of(self, key: ResourceKey) -> list[Edge]:
        """Incident edges sorted by identity."""
        return self._inner.edges_of(key)

    def neighbours(self, key: ResourceKey) -> list[ResourceKey]:
        """Neighbouring resource keys, sorted."""
        return self._inner.neighbours(key)

    def accounts(self) -> list[str]:
        """Sorted accounts of stored resources."""
        return self._inner.accounts()

    def _flush(self) -> None:
        _write(self._path / "resources.parquet",
               [resource.model_dump_json() for resource in self._inner.resources()])
        _write(self._path / "edges.parquet",
               [edge.model_dump_json() for edge in self._inner.edges()])

    def _load(self) -> None:
        for payload in _read(self._path / "resources.parquet"):
            self._inner.add_resource(Resource.model_validate_json(payload))
        for payload in _read(self._path / "edges.parquet"):
            self._inner.add_edge(Edge.model_validate_json(payload))


def _write(path: Path, payloads: list[str]) -> None:
    table = pa.table({"payload": pa.array(payloads, type=pa.string())})
    pq.write_table(table, path)


def _read(path: Path) -> list[str]:
    if not path.is_file():
        return []
    values = pq.read_table(path).column("payload").to_pylist()
    return [value for value in values if isinstance(value, str)]
