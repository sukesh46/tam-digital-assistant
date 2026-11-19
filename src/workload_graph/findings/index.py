"""Resource index and placement (Section 10.5).

Status: real
Purpose: answer "where in the workload map is this resource?" for an id or ARN from cost data.
Inputs: a ``BuildResult``, optional resource tags, and optionally the approved workload ids.
Outputs: ``ResourceIndex``, ``Placement``.
Planned dependencies: models.
Notes:
    - Every resource placed in the build (the partition invariant says each stored resource is in
      exactly one place) is indexed under its full ARN and its physical id (after the last ``/`` or
      ``:``), keyed by account.
    - Two different resources sharing a key in one account make that key ambiguous; ambiguous keys
      never match.
    - When ``approved`` is given, members of workloads that are not approved are ``unplaced``:
      rejected or unreviewed workloads count as unattributed spend (D46).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from workload_graph.ids import ResourceKey
from workload_graph.models import BuildResult, CandidateWorkload

PlacementKind = Literal["workload", "shared", "unplaced", "unknown", "ambiguous"]


@dataclass(frozen=True)
class Placement:
    kind: PlacementKind
    workload_id: str | None = None
    membership: str | None = None


def physical_id(arn: str) -> str:
    """The part of an ARN after its last ``/`` or ``:``."""
    return arn.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


class ResourceIndex:
    """Lookup from ``(account, id or ARN)`` to a placement."""

    def __init__(self, result: BuildResult, tags: Mapping[str, Mapping[str, str]] | None = None,
                 approved: Iterable[str] | None = None) -> None:
        approved_ids = None if approved is None else set(approved)
        self.workloads: dict[str, CandidateWorkload] = {}
        self._placements: dict[str, Placement] = {}
        self._tags = {key: dict(value) for key, value in (tags or {}).items()}
        self.notes: list[str] = []
        for workload in result.workloads:
            counted = approved_ids is None or workload.workload_id in approved_ids
            if counted:
                self.workloads[workload.workload_id] = workload
            for member in workload.members:
                self._placements[member.key.key_str()] = (
                    Placement("workload", workload.workload_id, member.membership) if counted
                    else Placement("unplaced"))
        for shared in result.shared_infrastructure:
            self._placements[shared.key.key_str()] = Placement("shared")
        for group in result.residual_groups:
            for unplaced in group.members:
                self._placements[unplaced.key.key_str()] = Placement("unplaced")
        for unplaced in result.unmapped:
            self._placements[unplaced.key.key_str()] = Placement("unplaced")

        self._keys: dict[str, ResourceKey] = {}
        for workload in result.workloads:
            for member in workload.members:
                self._keys[member.key.key_str()] = member.key
        for shared in result.shared_infrastructure:
            self._keys[shared.key.key_str()] = shared.key
        for group in result.residual_groups:
            for unplaced in group.members:
                self._keys[unplaced.key.key_str()] = unplaced.key
        for unplaced in result.unmapped:
            self._keys[unplaced.key.key_str()] = unplaced.key

        lookup: dict[tuple[str, str], set[str]] = {}
        for key_str in sorted(self._keys):
            key = self._keys[key_str]
            for candidate in {key.arn, physical_id(key.arn)}:
                lookup.setdefault((key.account_id, candidate), set()).add(key_str)
        self._lookup: dict[tuple[str, str], str] = {}
        self._ambiguous: set[tuple[str, str]] = set()
        for pair in sorted(lookup):
            if len(lookup[pair]) == 1:
                self._lookup[pair] = next(iter(lookup[pair]))
            else:
                self._ambiguous.add(pair)
                self.notes.append(f"resource id {pair[1]} in {pair[0]} names "
                                  f"{len(lookup[pair])} resources; it is not matched")

    def resolve(self, account_id: str, id_or_arn: str) -> str | None:
        """Key string for an id or ARN, or None when unknown or ambiguous."""
        for candidate in (id_or_arn, physical_id(id_or_arn)):
            pair = (account_id, candidate)
            if pair in self._ambiguous:
                return None
            if pair in self._lookup:
                return self._lookup[pair]
        return None

    def placement(self, account_id: str, id_or_arn: str) -> Placement:
        """Where an id or ARN sits in the build."""
        for candidate in (id_or_arn, physical_id(id_or_arn)):
            pair = (account_id, candidate)
            if pair in self._ambiguous:
                return Placement("ambiguous")
            if pair in self._lookup:
                return self._placements[self._lookup[pair]]
        return Placement("unknown")

    def tags_of(self, key: ResourceKey) -> dict[str, str]:
        """Tags of a resource, if known."""
        return dict(self._tags.get(key.key_str(), {}))

    def account_resource_counts(self, account_id: str) -> tuple[int, int]:
        """``(resources outside any counted workload, all resources)`` in an account."""
        total = outside = 0
        for key_str, key in self._keys.items():
            if key.account_id != account_id:
                continue
            total += 1
            if self._placements[key_str].kind != "workload":
                outside += 1
        return outside, total
