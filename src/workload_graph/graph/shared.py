"""Shared infrastructure detection.

Status: real
Purpose: find hub resources (VPCs, subnets, security groups, load balancers, target groups)
    that serve members of several seeds, so they are reported as shared instead of merging
    workloads or being attached to one of them.
Inputs: the store, seeds, ruleset (``shared_min_clusters``), excluded key strings.
Outputs: ``{hub key: sorted seed ids it touches}``.
Planned dependencies: none.
Notes:
    - Only undeclared resources can be shared; a declared hub stays in its workload.
    - Direction matters: a container is touched by incoming container edges from members; a
      load balancer or target group touches members through outgoing ``LB_TARGET`` edges. A
      consumer that merely sits in two workloads' containers is not a hub.
"""

from __future__ import annotations

from workload_graph.graph.seeds import Seed
from workload_graph.ids import ResourceKey
from workload_graph.models import CONTAINER_EDGE_TYPES, EdgeType
from workload_graph.ruleset import Ruleset
from workload_graph.store.base import GraphStore


def seed_index(seeds: list[Seed]) -> dict[str, str]:
    """Map member key string to seed id."""
    return {m.key_str(): s.seed_id for s in seeds for m in s.members}


def find_shared(store: GraphStore, seeds: list[Seed], ruleset: Ruleset,
                excluded: set[str] | None = None) -> dict[ResourceKey, list[str]]:
    """Return shared hubs and the seeds they touch, sorted by key."""
    member_of = seed_index(seeds)
    skip = excluded or set()
    shared: dict[ResourceKey, list[str]] = {}
    for resource in store.resources():
        key = resource.key
        if key.key_str() in member_of or key.key_str() in skip:
            continue
        touched: set[str] = set()
        for edge in store.edges_of(key):
            if not isinstance(edge.dst, ResourceKey) or edge.src == edge.dst:
                continue
            if edge.edge_type in CONTAINER_EDGE_TYPES and edge.dst == key:
                other = edge.src
            elif edge.edge_type is EdgeType.LB_TARGET and edge.src == key:
                other = edge.dst
            else:
                continue
            seed = member_of.get(other.key_str())
            if seed is not None:
                touched.add(seed)
        if len(touched) >= ruleset.shared_min_clusters:
            shared[key] = sorted(touched)
    return dict(sorted(shared.items()))
