"""Output models: candidate workloads and the build result.

Status: real
Purpose: the proposal handed to a human for confirmation.
Inputs: graph builder output.
Outputs: ``CandidateWorkload``, ``Member``, ``SharedInfra``, ``ResidualGroup``, ``Unplaced``,
    ``ExternalRefSummary``, ``PendingRef``, ``BuildResult``.
Planned dependencies: pydantic.
Notes: the builder is responsible for stable list ordering; ``BuildResult.to_json`` adds
    sorted keys so the serialised form is byte-identical for identical input.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from workload_graph.ids import AccountId, ResourceKey
from workload_graph.models.edge import EdgeType
from workload_graph.models.evidence import Evidence
from workload_graph.models.probe import ProbeResult
from workload_graph.models.resource import ExternalReason, ExternalRef

Confidence = Literal["high", "medium", "low"]
UnplacedReason = Literal["below_threshold", "ambiguous", "no_edges", "tag_conflict"]


class Member(BaseModel):
    """A resource's membership in a candidate workload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: ResourceKey
    membership: Literal["declared", "attached"]
    affinity: float | None = None
    reasons: list[EdgeType]
    evidence: list[Evidence]


class WorkloadEdge(BaseModel):
    """One stored edge whose source is a member of a workload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    src: ResourceKey
    dst: str
    edge_type: EdgeType
    evidence: list[Evidence]


class CandidateWorkload(BaseModel):
    """A proposed workload. A human confirms it; it is never a verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workload_id: str
    name_hint: str
    members: list[Member]
    confidence: Confidence
    accounts: list[AccountId]
    sources_used: list[str]
    shared_dependencies: list[ResourceKey]
    external_refs: list[ExternalRef]
    evidence_summary: dict[str, int]
    edges: list[WorkloadEdge] = []


class SharedInfra(BaseModel):
    """Infrastructure linked to several workloads; never merged into one of them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: ResourceKey
    touches: list[str]


class Unplaced(BaseModel):
    """A resource not placed in any workload, with the reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: ResourceKey
    reason: UnplacedReason
    candidates: list[str] = []
    best_affinity: float | None = None


class ResidualGroup(BaseModel):
    """Unplaced resources grouped by ``(account, region, vpc)``. Always low confidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: AccountId
    region: str
    vpc_id: str
    members: list[Unplaced]
    confidence: Literal["low"] = "low"


class ExternalRefSummary(BaseModel):
    """One external account referenced from the approved scope, with a reference count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: AccountId
    reason: ExternalReason
    reference_count: int
    is_finding: bool


class PendingRef(BaseModel):
    """A cross-account edge into an approved account whose end is not (yet) collected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    src: ResourceKey
    dst: ResourceKey
    reason: str


class BuildResult(BaseModel):
    """Everything a build produced, plus what is needed to reproduce it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ruleset_version: str
    ruleset_hash: str
    workloads: list[CandidateWorkload]
    shared_infrastructure: list[SharedInfra]
    residual_groups: list[ResidualGroup]
    unmapped: list[Unplaced]
    external_refs: list[ExternalRefSummary]
    pending_refs: list[PendingRef] = []
    probe: ProbeResult
    notes: list[str]
    generated_from: list[str]

    def to_json(self) -> str:
        """Serialise with sorted keys and a trailing newline (byte-stable)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
