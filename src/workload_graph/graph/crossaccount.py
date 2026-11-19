"""Cross-account edge resolution.

Status: real
Purpose: classify every ``CROSS_ACCOUNT_REF`` edge as resolved, pending or external.
Inputs: the store, the approved account list, ``known_aws_service_account_ids``.
Outputs: ``CrossAccountReport``.
Planned dependencies: none.
Notes:
    - # RISK: a reference to another account is metadata, never permission. This module has
      no AWS access of any kind and must never gain it.
    - Resolved: the destination resource is in the store. Pending: the destination account is
      approved but the resource is not (yet) collected. External: the account is AWS-owned
      (not a finding) or outside the approved scope (a finding).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from workload_graph.ids import ResourceKey
from workload_graph.models import (
    Edge,
    EdgeType,
    ExternalReason,
    ExternalRef,
    ExternalRefSummary,
    PendingRef,
)
from workload_graph.store.base import GraphStore


@dataclass(frozen=True)
class CrossAccountReport:
    """Outcome of resolving cross-account references, in sorted order."""

    resolved: tuple[Edge, ...]
    pending: tuple[PendingRef, ...]
    external: tuple[tuple[ResourceKey, ExternalRef], ...]
    summaries: tuple[ExternalRefSummary, ...]
    notes: tuple[str, ...]

    def findings(self) -> list[ExternalRefSummary]:
        """External accounts that should be reported to a TAM."""
        return [s for s in self.summaries if s.is_finding]

    def for_members(
        self, members: Iterable[ResourceKey]
    ) -> tuple[list[ResourceKey], list[ExternalRef]]:
        """Dependencies (resolved or pending keys) and external refs of a member set."""
        member_set = set(members)
        deps: set[ResourceKey] = set()
        for edge in self.resolved:
            if edge.src in member_set and isinstance(edge.dst, ResourceKey):
                deps.add(edge.dst)
        for ref in self.pending:
            if ref.src in member_set:
                deps.add(ref.dst)
        externals = {ext for src, ext in self.external if src in member_set}
        return sorted(deps - member_set), sorted(externals, key=ExternalRef.sort_key)


def resolve_cross_account(
    store: GraphStore,
    approved_account_ids: Sequence[str],
    known_aws_service_account_ids: Sequence[str],
) -> CrossAccountReport:
    """Classify every cross-account edge in ``store``. Never contacts any account."""
    approved = set(approved_account_ids)
    aws_owned = set(known_aws_service_account_ids)
    collected = set(store.accounts())
    resolved: list[Edge] = []
    pending: list[PendingRef] = []
    external: list[tuple[ResourceKey, ExternalRef]] = []

    for edge in store.edges():
        if edge.edge_type is not EdgeType.CROSS_ACCOUNT_REF:
            continue
        dst = edge.dst
        if isinstance(dst, ExternalRef):
            external.append((edge.src, dst))
        elif store.has_resource(dst):
            resolved.append(edge)
        elif dst.account_id in aws_owned:
            external.append((edge.src, ExternalRef(account_id=dst.account_id,
                                                   reason="aws_owned", arn=dst.arn)))
        elif dst.account_id in approved:
            reason = ("approved account not collected" if dst.account_id not in collected
                      else "resource not found in collected data")
            pending.append(PendingRef(src=edge.src, dst=dst, reason=reason))
        else:
            external.append((edge.src, ExternalRef(account_id=dst.account_id,
                                                   reason="out_of_scope", arn=dst.arn)))

    counts: dict[tuple[str, ExternalReason], int] = {}
    for _, ref in external:
        counts[(ref.account_id, ref.reason)] = counts.get((ref.account_id, ref.reason), 0) + 1
    summaries = tuple(
        ExternalRefSummary(account_id=account, reason=reason,
                           reference_count=count, is_finding=reason != "aws_owned")
        for (account, reason), count in sorted(counts.items())
    )
    notes = [f"cross-account: {len(pending)} reference(s) pending into approved accounts "
             f"not yet collected or not found"] if pending else []
    notes += [f"cross-account: account {s.account_id} referenced {s.reference_count} time(s) "
              f"({s.reason})" for s in summaries if s.is_finding]
    return CrossAccountReport(
        resolved=tuple(resolved),
        pending=tuple(sorted(pending, key=lambda p: (p.src.key_str(), p.dst.key_str()))),
        external=tuple(sorted(external, key=lambda x: (x[0].key_str(), x[1].sort_key()))),
        summaries=summaries,
        notes=tuple(notes),
    )
