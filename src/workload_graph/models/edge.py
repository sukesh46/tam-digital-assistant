"""Edge types and edges between resources.

Status: real
Purpose: typed relationships, each backed by at least one evidence record.
Inputs: normaliser output.
Outputs: ``EdgeType``, ``Edge``, ``GROUPING_EDGE_TYPES``, ``CONTAINER_EDGE_TYPES``.
Planned dependencies: pydantic.
Notes: see docs/EDGE_TYPES.md. Container edges point resource -> container.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from workload_graph.ids import ResourceKey
from workload_graph.models.evidence import Evidence
from workload_graph.models.resource import ExternalRef


class EdgeType(StrEnum):
    """Kinds of relationship between resources."""

    STACK_MEMBER = "STACK_MEMBER"
    TAG_WORKLOAD_KEY = "TAG_WORKLOAD_KEY"
    LB_TARGET = "LB_TARGET"
    SAME_SUBNET = "SAME_SUBNET"
    SHARED_SECURITY_GROUP = "SHARED_SECURITY_GROUP"
    SAME_VPC = "SAME_VPC"
    CREATED_BY_SAME_PRINCIPAL = "CREATED_BY_SAME_PRINCIPAL"
    CROSS_ACCOUNT_REF = "CROSS_ACCOUNT_REF"


GROUPING_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.STACK_MEMBER, EdgeType.TAG_WORKLOAD_KEY}
)
CONTAINER_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SAME_SUBNET, EdgeType.SAME_VPC, EdgeType.SHARED_SECURITY_GROUP}
)
HUB_EDGE_TYPES: frozenset[EdgeType] = CONTAINER_EDGE_TYPES | {EdgeType.LB_TARGET}


class Edge(BaseModel):
    """A directed, typed, evidenced relationship."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    src: ResourceKey
    dst: ResourceKey | ExternalRef
    edge_type: EdgeType
    evidence: tuple[Evidence, ...] = Field(min_length=1)

    def dst_str(self) -> str:
        """String form of the destination for sorting and display."""
        if isinstance(self.dst, ResourceKey):
            return self.dst.key_str()
        return self.dst.sort_key()

    def identity(self) -> tuple[str, str, str]:
        """``(src, dst, type)``: edges with equal identity are merged."""
        return (self.src.key_str(), self.dst_str(), self.edge_type.value)

    def merged_with(self, other: Edge) -> Edge:
        """Merge evidence of two edges with equal identity, without duplicates, sorted."""
        if other.identity() != self.identity():
            raise ValueError("cannot merge edges with different identity")
        evidence = sorted(set(self.evidence) | set(other.evidence), key=Evidence.sort_key)
        return self.model_copy(update={"evidence": tuple(evidence)})
