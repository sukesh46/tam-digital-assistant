"""Graph stores.

Status: real
Purpose: the ``GraphStore`` contract, an in-memory implementation and an optional Parquet one.
Inputs: none.
Outputs: ``InMemoryGraphStore``. Parquet is imported from ``workload_graph.store.parquet``.
Planned dependencies: networkx, optional pyarrow.
Notes: parquet is an optional extra (D14) and is not imported here, so the core install
    does not require pyarrow.
"""

from workload_graph.store.memory import InMemoryGraphStore

__all__ = ["InMemoryGraphStore"]
