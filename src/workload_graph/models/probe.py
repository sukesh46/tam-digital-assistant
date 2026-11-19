"""Capability probe result models.

Status: real
Purpose: report which sources were available per account, so a TAM can see what contributed.
Inputs: snapshot statuses (see ``workload_graph.probe``).
Outputs: ``ProbeResult``, ``ProbeEntry``, ``SourceAvailability``.
Planned dependencies: pydantic.
Notes: entries are sorted by (account, source).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from workload_graph.ids import AccountId
from workload_graph.models.snapshot import SourceName

SourceAvailability = Literal["available", "empty", "denied", "partial", "error"]


class ProbeEntry(BaseModel):
    """Availability of one source in one account."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: AccountId
    source: SourceName
    availability: SourceAvailability
    notes: list[str] = []


class ProbeResult(BaseModel):
    """Availability of every source in every account in scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: list[ProbeEntry] = []
    notes: list[str] = []

    def availability(self, account_id: str, source: str) -> SourceAvailability | None:
        """Look up the availability for one account and source, if probed."""
        for entry in self.entries:
            if entry.account_id == account_id and entry.source == source:
                return entry.availability
        return None
