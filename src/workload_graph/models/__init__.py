"""Domain models (immutable, validated, JSON-serialisable).

Status: real
Purpose: re-export the domain models.
Inputs: none.
Outputs: model classes.
Planned dependencies: pydantic.
Notes: all models are frozen and use timezone-aware UTC datetimes.
"""

from workload_graph.models.edge import (
    CONTAINER_EDGE_TYPES,
    GROUPING_EDGE_TYPES,
    HUB_EDGE_TYPES,
    Edge,
    EdgeType,
)
from workload_graph.models.evidence import Evidence
from workload_graph.models.probe import ProbeEntry, ProbeResult, SourceAvailability
from workload_graph.models.resource import ExternalReason, ExternalRef, Resource
from workload_graph.models.snapshot import (
    COST_SOURCES,
    SOURCES,
    RawSnapshot,
    SnapshotStatus,
    SourceName,
)
from workload_graph.models.workload import (
    BuildResult,
    CandidateWorkload,
    Confidence,
    ExternalRefSummary,
    Member,
    PendingRef,
    ResidualGroup,
    SharedInfra,
    Unplaced,
    UnplacedReason,
    WorkloadEdge,
)
from workload_graph.models.workspace import Workspace

__all__ = [
    "CONTAINER_EDGE_TYPES",
    "COST_SOURCES",
    "GROUPING_EDGE_TYPES",
    "HUB_EDGE_TYPES",
    "SOURCES",
    "BuildResult",
    "CandidateWorkload",
    "Confidence",
    "Edge",
    "EdgeType",
    "Evidence",
    "ExternalReason",
    "ExternalRef",
    "ExternalRefSummary",
    "Member",
    "PendingRef",
    "ProbeEntry",
    "ProbeResult",
    "RawSnapshot",
    "ResidualGroup",
    "Resource",
    "SharedInfra",
    "SnapshotStatus",
    "SourceAvailability",
    "SourceName",
    "Unplaced",
    "UnplacedReason",
    "WorkloadEdge",
    "Workspace",
]
