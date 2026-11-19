"""Resource and ExternalRef models.

Status: real
Purpose: normalised resource records and unresolved or out-of-scope endpoints.
Inputs: normaliser output.
Outputs: ``Resource``, ``ExternalRef``, ``ExternalReason``.
Planned dependencies: pydantic, ids.
Notes: ``sources`` serialises as a sorted list so output does not depend on set ordering.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer

from workload_graph.ids import AccountId, ResourceKey
from workload_graph.models.snapshot import SourceName

ExternalReason = Literal["out_of_scope", "aws_owned", "unknown"]


class Resource(BaseModel):
    """A normalised resource seen by one or more sources."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: ResourceKey
    resource_type: str
    name: str | None = None
    tags: dict[str, str] = {}
    attributes: dict[str, str] = {}
    sources: frozenset[SourceName] = frozenset()

    @field_serializer("sources")
    def _sorted_sources(self, value: frozenset[str]) -> list[str]:
        return sorted(value)

    def merged_with(self, other: Resource) -> Resource:
        """Merge two records of the same resource; the result is order-independent.

        Sources and tags are unioned. On conflicting values the lexicographically smallest
        wins, so merge order never changes the result.
        """
        if other.key != self.key:
            raise ValueError("cannot merge records of different resources")
        names = sorted(n for n in (self.name, other.name) if n is not None)
        types = sorted({self.resource_type, other.resource_type},
                       key=lambda t: (not t.startswith("AWS::"), t))
        return Resource(
            key=self.key,
            resource_type=types[0],
            name=names[0] if names else None,
            tags=_merge_str_dicts(self.tags, other.tags),
            attributes=_merge_str_dicts(self.attributes, other.attributes),
            sources=self.sources | other.sources,
        )


def _merge_str_dicts(a: dict[str, str], b: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key in sorted(set(a) | set(b)):
        values = sorted(v for v in (a.get(key), b.get(key)) if v is not None)
        merged[key] = values[0]
    return merged


class ExternalRef(BaseModel):
    """An endpoint in an account outside the approved scope, or otherwise unresolved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: AccountId
    reason: ExternalReason
    arn: str | None = None

    def sort_key(self) -> str:
        """Stable ordering key."""
        return f"external|{self.account_id}|{self.reason}|{self.arn or ''}"
