"""Versioned, hashed ruleset for the graph builder.

Status: real
Purpose: every tunable weight and threshold in one place, versioned and hashed (D12) so any
    result can be reproduced and compared across rule changes.
Inputs: JSON file or defaults.
Outputs: ``Ruleset``.
Planned dependencies: pydantic.
Notes: every default is provisional and chosen for relative ordering only, not calibration.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from workload_graph.ids import AccountId
from workload_graph.models.edge import GROUPING_EDGE_TYPES, EdgeType

UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]

RULESET_VERSION = "0.1.0"


def _default_weights() -> dict[EdgeType, float]:
    # SKELETON: provisional, tune with golden scenarios
    return {
        EdgeType.LB_TARGET: 0.8,
        EdgeType.SAME_SUBNET: 0.4,
        EdgeType.SHARED_SECURITY_GROUP: 0.4,
        EdgeType.SAME_VPC: 0.2,
        EdgeType.CREATED_BY_SAME_PRINCIPAL: 0.3,
        EdgeType.CROSS_ACCOUNT_REF: 0.0,
    }


class Ruleset(BaseModel):
    """Weights, thresholds and source settings used by normalisers and the builder."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    version: str = RULESET_VERSION
    # SKELETON: provisional, tune with golden scenarios. Customers vary.
    workload_tag_keys: list[str] = ["app", "application", "workload", "project", "service"]
    # SKELETON: provisional, tune with golden scenarios
    edge_weights: dict[EdgeType, UnitFloat] = Field(default_factory=_default_weights)
    # SKELETON: provisional, tune with golden scenarios
    attach_threshold: UnitFloat = 0.6
    # SKELETON: provisional, tune with golden scenarios
    attach_margin: UnitFloat = 0.15
    # SKELETON: provisional, tune with golden scenarios
    confidence_high_declared_ratio: UnitFloat = 0.8
    # SKELETON: provisional, tune with golden scenarios
    confidence_medium_declared_ratio: UnitFloat = 0.5
    # SKELETON: provisional, tune with golden scenarios
    shared_min_clusters: int = Field(default=2, ge=2)
    # SKELETON: provisional, tune with golden scenarios
    cloudtrail_window_minutes: int = Field(default=30, ge=1)
    # ASSUMPTION: CloudTrail event history covers 90 days of management events; verify.
    cloudtrail_lookback_days: int = Field(default=90, ge=1, le=90)
    # SKELETON: provisional, tune with golden scenarios
    cloudtrail_max_calls_per_account: int = Field(default=200, ge=1)
    # SKELETON: provisional and incomplete by design
    # ASSUMPTION: Lambda records CreateFunction with a versioned event name; both are listed.
    creation_event_names: list[str] = [
        "CreateBucket",
        "CreateDBInstance",
        "CreateFunction20150331",
        "CreateFunction",
        "CreateLoadBalancer",
        "CreateTable",
        "RunInstances",
    ]
    # TODO(input-needed): fill from real data (AWS service-owned account ids seen in customers).
    known_aws_service_account_ids: list[AccountId] = []

    @field_validator("workload_tag_keys")
    @classmethod
    def _normalise_tag_keys(cls, value: list[str]) -> list[str]:
        return sorted({key.lower() for key in value})

    @field_validator("creation_event_names", "known_aws_service_account_ids")
    @classmethod
    def _sorted_unique(cls, value: list[str]) -> list[str]:
        return sorted(set(value))

    @field_validator("edge_weights")
    @classmethod
    def _no_grouping_weights(cls, value: dict[EdgeType, float]) -> dict[EdgeType, float]:
        bad = sorted(t.value for t in value if t in GROUPING_EDGE_TYPES)
        if bad:
            raise ValueError(f"grouping declarations are not weighted: {bad}")
        return value

    @model_validator(mode="after")
    def _ratios_ordered(self) -> Ruleset:
        if self.confidence_medium_declared_ratio > self.confidence_high_declared_ratio:
            raise ValueError("confidence_medium_declared_ratio must be <= high ratio")
        return self

    def weight(self, edge_type: EdgeType) -> float:
        """Weight for a non-grouping edge type; missing types weigh 0.0."""
        return float(self.edge_weights.get(edge_type, 0.0))

    def canonical_json(self) -> str:
        """Canonical JSON: sorted keys, no whitespace."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        """SHA-256 hex digest of the canonical JSON."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def save(self, path: Path) -> None:
        """Write the ruleset as pretty, sorted JSON."""
        data = self.model_dump(mode="json")
        path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Ruleset:
        """Load and validate a ruleset JSON file."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
