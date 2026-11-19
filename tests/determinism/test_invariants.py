"""Step 11: partition and evidence invariants, on golden, synthetic and generated inputs."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wg_synthetic import synthetic_snapshots, synthetic_workspace
from wg_test_helpers import (
    ACCT_A,
    SCENARIOS,
    build_from_snapshots,
    gedge,
    gk,
    gres,
    placed_keys,
    scenario_snapshots,
    scenario_workspace,
)

from workload_graph.graph.build import build_workloads
from workload_graph.models import EdgeType, ProbeResult, Workspace
from workload_graph.ruleset import Ruleset
from workload_graph.store.memory import InMemoryGraphStore

pytestmark = pytest.mark.determinism


def _assert_partition(result: Any, store: Any) -> None:
    placed = placed_keys(result)
    duplicates = [k for k, n in Counter(placed).items() if n > 1]
    assert duplicates == [], f"placed more than once: {duplicates[:5]}"
    assert sorted(placed) == sorted(r.key for r in store.resources())


def _assert_evidence(result: Any) -> None:
    snapshot_ids = set(result.generated_from)
    for workload in result.workloads:
        for member in workload.members:
            assert member.evidence, f"{member.key.key_str()} has no evidence"
            assert member.reasons, f"{member.key.key_str()} has no reasons"
            for ev in member.evidence:
                assert ev.raw_ref.split("#", 1)[0] in snapshot_ids, ev.raw_ref
                assert ev.raw_ref.split("#", 1)[1].startswith("/payload/"), ev.raw_ref


@pytest.mark.parametrize("name", SCENARIOS)
def test_partition_invariant_holds_for_every_golden_scenario(name: str) -> None:
    result, store = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))
    _assert_partition(result, store)


@pytest.mark.parametrize("name", SCENARIOS)
def test_evidence_invariant_holds_for_every_golden_scenario(name: str) -> None:
    result, _ = build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))
    _assert_evidence(result)


@pytest.mark.slow
def test_large_synthetic_scenario_completes_quickly_and_keeps_invariants() -> None:
    snapshots = synthetic_snapshots()
    start = time.perf_counter()
    result, store = build_from_snapshots(snapshots, synthetic_workspace())
    elapsed = time.perf_counter() - start
    assert len(store.resources()) >= 1800
    assert elapsed < 60, f"S12 took {elapsed:.1f}s"
    _assert_partition(result, store)
    _assert_evidence(result)
    assert len(result.workloads) >= 100
    assert result.shared_infrastructure


EDGE_TYPES = [EdgeType.STACK_MEMBER, EdgeType.TAG_WORKLOAD_KEY, EdgeType.LB_TARGET,
              EdgeType.SAME_SUBNET, EdgeType.SHARED_SECURITY_GROUP, EdgeType.SAME_VPC,
              EdgeType.CREATED_BY_SAME_PRINCIPAL, EdgeType.CROSS_ACCOUNT_REF]

graphs = st.integers(min_value=1, max_value=25).flatmap(lambda n: st.tuples(
    st.just(n),
    st.lists(st.tuples(st.integers(0, n - 1), st.integers(0, n - 1),
                       st.sampled_from(EDGE_TYPES)), max_size=60),
    st.sets(st.integers(0, n - 1), max_size=3),
    st.lists(st.sampled_from(["vpc-1", "vpc-2", ""]), min_size=n, max_size=n),
))


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(graph=graphs)
def test_partition_invariant_holds_for_generated_graphs(graph: Any) -> None:
    n, edges, conflicted, vpcs = graph
    store = InMemoryGraphStore()
    for i in range(n):
        attrs = {"vpc_id": vpcs[i]} if vpcs[i] else {}
        if i in conflicted:
            attrs["tag_conflict"] = "true"
        store.add_resource(gres(gk(f"r{i:02d}"), **attrs))
    for i, (a, b, edge_type) in enumerate(edges):
        store.add_edge(gedge(gk(f"r{a:02d}"), gk(f"r{b:02d}"), edge_type, ref=f"g#/payload/{i}",
                             detail="workload tag app=x"))
    workspace = Workspace(workspace_id="w", customer="example-customer",
                          approved_account_ids=[ACCT_A])
    result = build_workloads(store, Ruleset(), workspace, ProbeResult(), ["g"])
    _assert_partition(result, store)
    for workload in result.workloads:
        assert all(m.evidence for m in workload.members)
