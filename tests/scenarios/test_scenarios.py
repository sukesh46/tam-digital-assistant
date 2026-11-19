"""Golden scenarios S1–S12: expected behaviour from the specification, not perfect scores."""

from __future__ import annotations

import time
from collections import Counter

import pytest
from wg_synthetic import synthetic_snapshots, synthetic_workspace
from wg_test_helpers import build_from_snapshots, scenario_snapshots, scenario_workspace

pytestmark = pytest.mark.scenario


def _build(name: str):
    return build_from_snapshots(scenario_snapshots(name), scenario_workspace(name))[0]


def test_s1_two_stacks_become_two_high_confidence_workloads() -> None:
    result = _build("s1")
    assert sorted(w.name_hint for w in result.workloads) == ["billing", "orders"]
    assert {w.confidence for w in result.workloads} == {"high"}
    assert any("vpc-0s1" in s.key.arn for s in result.shared_infrastructure)


def test_s2_tag_groups_become_two_high_confidence_workloads() -> None:
    result = _build("s2")
    assert sorted(w.name_hint for w in result.workloads) == ["billing", "orders"]
    assert {w.confidence for w in result.workloads} == {"high"}
    assert all("tags" in w.sources_used for w in result.workloads)
    assert all("stacks" not in w.sources_used for w in result.workloads)


def test_s3_tag_conflict_is_excluded_and_the_workloads_stay_separate() -> None:
    result = _build("s3")
    assert len(result.workloads) == 2
    conflicted = [u for u in result.unmapped if u.reason == "tag_conflict"]
    assert len(conflicted) == 1
    assert conflicted[0].key.arn.endswith("i-0s3both")
    assert all("i-0s3both" not in m.key.arn for w in result.workloads for m in w.members)


def test_s4_shared_load_balancer_touches_both_workloads() -> None:
    result = _build("s4")
    assert len(result.workloads) == 2
    shared = [s for s in result.shared_infrastructure if "shared-alb" in s.key.arn]
    assert len(shared) == 1
    assert shared[0].touches == sorted(w.workload_id for w in result.workloads)


def test_s5_undeclared_network_becomes_low_confidence_residual_groups() -> None:
    result = _build("s5")
    assert result.workloads == []
    assert {g.vpc_id for g in result.residual_groups} == {"vpc-0s5a", "vpc-0s5b"}
    assert {g.confidence for g in result.residual_groups} == {"low"}


def test_s6_undeclared_instance_attaches_with_affinity_and_reasons() -> None:
    result = _build("s6")
    assert len(result.workloads) == 1
    workload = result.workloads[0]
    attached = [m for m in workload.members if m.key.arn.endswith("i-0s6new")]
    assert len(attached) == 1
    assert attached[0].membership == "attached"
    assert attached[0].affinity == pytest.approx(0.7984)
    assert workload.confidence in {"low", "medium"}
    reasons = {r.value for r in attached[0].reasons}
    assert {"CREATED_BY_SAME_PRINCIPAL", "SAME_SUBNET", "SHARED_SECURITY_GROUP"} <= reasons


def test_s7_nested_stack_resources_stay_with_the_child_and_the_parent_link_is_recorded() -> None:
    result = _build("s7")
    by_name = {w.name_hint: w for w in result.workloads}
    assert set(by_name) == {"platform", "platform-Network-XYZ"}
    child_arns = [m.key.arn for m in by_name["platform-Network-XYZ"].members]
    assert any(arn.endswith("vpc/vpc-0s7") for arn in child_arns)
    assert all("vpc-0s7" not in m.key.arn for m in by_name["platform"].members)
    assert any("nested stack" in note and "platform-Network-XYZ" in note for note in result.notes)


def test_s8_approved_peer_resolves_and_unapproved_peer_is_an_external_finding() -> None:
    result = _build("s8")
    assert len(result.workloads) == 1
    workload = result.workloads[0]
    assert any(d.account_id == "222222222222" for d in workload.shared_dependencies)
    assert any(e.account_id == "333333333333" and e.reason == "out_of_scope"
               for e in workload.external_refs)
    assert result.pending_refs == []
    assert any(s.account_id == "333333333333" and s.is_finding for s in result.external_refs)
    assert all(s.account_id != "222222222222" for s in result.external_refs)


def test_s9_denied_cloudtrail_is_reported_and_workloads_are_still_built() -> None:
    result = _build("s9")
    assert result.workloads
    assert result.probe.availability("111111111111", "cloudtrail") == "denied"
    assert any("cloudtrail denied in 111111111111" == note for note in result.notes)


def test_s10_empty_account_has_an_empty_result_and_empty_probe() -> None:
    result = _build("s10")
    assert result.workloads == []
    assert result.shared_infrastructure == []
    assert result.residual_groups == []
    assert result.unmapped == []
    assert result.probe.entries
    assert {entry.availability for entry in result.probe.entries} == {"empty"}


def test_s11_equally_linked_resource_is_not_attached_and_is_reported_ambiguous() -> None:
    result = _build("s11")
    assert len(result.workloads) == 2
    assert all("i-0s11mystery" not in m.key.arn for w in result.workloads for m in w.members)
    mystery = [m for g in result.residual_groups for m in g.members
               if m.key.arn.endswith("i-0s11mystery")]
    assert len(mystery) == 1
    assert mystery[0].reason == "ambiguous"
    assert any(note.startswith("ambiguous:") and "i-0s11mystery" in note for note in result.notes)


@pytest.mark.slow
def test_s12_large_synthetic_case_finishes_and_partitions_every_resource() -> None:
    snapshots = synthetic_snapshots()
    started = time.perf_counter()
    result, store = build_from_snapshots(snapshots, synthetic_workspace())
    assert time.perf_counter() - started < 60
    placed = [m.key for w in result.workloads for m in w.members]
    placed += [s.key for s in result.shared_infrastructure]
    placed += [m.key for g in result.residual_groups for m in g.members]
    placed += [u.key for u in result.unmapped]
    assert [k for k, n in Counter(placed).items() if n > 1] == []
    assert sorted(placed) == sorted(r.key for r in store.resources())
    assert len(store.resources()) >= 1800
