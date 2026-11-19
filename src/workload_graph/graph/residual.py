"""Residual groups and unmapped resources.

Status: real
Purpose: group resources that were neither declared, attached nor shared by
    ``(account, region, vpc)``; everything without a VPC is unmapped.
Inputs: unplaced resources (with reasons), the store, shared hub keys.
Outputs: ``(residual groups, unmapped)`` both sorted.
Planned dependencies: none.
Notes: residual groups are always low confidence; shared infrastructure is never included.
"""

from __future__ import annotations

from collections.abc import Iterable

from workload_graph.ids import ResourceKey
from workload_graph.models import ResidualGroup, Unplaced
from workload_graph.store.base import GraphStore


def build_residuals(unplaced: Iterable[Unplaced], store: GraphStore,
                    shared: set[ResourceKey]) -> tuple[list[ResidualGroup], list[Unplaced]]:
    """Split unplaced resources into per-VPC residual groups and unmapped resources."""
    groups: dict[tuple[str, str, str], list[Unplaced]] = {}
    unmapped: list[Unplaced] = []
    for item in unplaced:
        if item.key in shared:
            continue
        resource = store.get_resource(item.key)
        vpc = resource.attributes.get("vpc_id") if resource else None
        if vpc:
            groups.setdefault((item.key.account_id, item.key.region, vpc), []).append(item)
        else:
            unmapped.append(item)
    residual = [
        ResidualGroup(account_id=account, region=region, vpc_id=vpc,
                      members=sorted(members, key=lambda u: u.key))
        for (account, region, vpc), members in sorted(groups.items())
    ]
    return residual, sorted(unmapped, key=lambda u: u.key)
