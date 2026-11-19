"""Raw snapshots produced by collectors.

Status: real
Purpose: replayable record of exactly what a collector saw (D8).
Inputs: collector output or JSON files on disk.
Outputs: ``RawSnapshot``, ``SourceName``, ``SnapshotStatus``, ``SOURCES``, ``COST_SOURCES``.
Planned dependencies: pydantic.
Notes: payload maps operation name to the list of response pages. Payload keys and page keys
    starting with ``_`` are collector metadata (for example request context such as the
    target group a ``DescribeTargetHealth`` page belongs to), not API response data.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from workload_graph.ids import AccountId

SourceName = Literal["tags", "stacks", "topology", "cloudtrail", "cost_explorer",
                     "cost_optimization_hub"]
SnapshotStatus = Literal["ok", "empty", "denied", "partial", "error"]
# The graph sources. The cost sources are read by the findings pipeline only.
SOURCES: tuple[SourceName, ...] = ("cloudtrail", "stacks", "tags", "topology")
COST_SOURCES: tuple[SourceName, ...] = ("cost_explorer", "cost_optimization_hub")


class RawSnapshot(BaseModel):
    """One collector run for one source, account and region."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    source: SourceName
    account_id: AccountId
    region: str
    collected_at: AwareDatetime
    operations_called: list[str]
    # Any: pages are raw boto3 response dicts whose shape is defined by botocore models.
    payload: dict[str, list[dict[str, Any]]]
    status: SnapshotStatus
    notes: list[str] = []

    @field_validator("collected_at")
    @classmethod
    def _to_utc(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)

    def has_data(self) -> bool:
        """True if any non-metadata page holds a non-empty list."""
        for op, pages in sorted(self.payload.items()):
            if op.startswith("_"):
                continue
            for page in pages:
                for key, value in page.items():
                    if key.startswith("_"):
                        continue
                    if isinstance(value, list) and value:
                        return True
        return False
