"""Evidence: where a fact came from.

Status: real
Purpose: trace every grouping back to a source, time and raw pointer.
Inputs: normaliser output.
Outputs: ``Evidence``.
Planned dependencies: pydantic.
Notes: ``detail`` is short and never holds secrets or full API payloads.
"""

from __future__ import annotations

from datetime import UTC

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from workload_graph.models.snapshot import SourceName

MAX_DETAIL_LENGTH = 200


class Evidence(BaseModel):
    """A pointer to the original datum supporting a resource or edge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceName
    observed_at: AwareDatetime
    raw_ref: str = Field(description="'{snapshot_id}#{json_pointer}' to the original datum")
    detail: str = Field(max_length=MAX_DETAIL_LENGTH)

    @field_validator("observed_at")
    @classmethod
    def _to_utc(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)

    def sort_key(self) -> tuple[str, str, str, str]:
        """Stable ordering key."""
        return (self.source, self.raw_ref, self.detail, self.observed_at.isoformat())
