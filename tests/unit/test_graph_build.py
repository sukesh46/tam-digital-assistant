"""Step 10.7: build_workloads orchestration."""

from __future__ import annotations

from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C, gedge, gk, gres

from workload_graph.graph.build import build_workloads
from workload_graph.graph.seeds import seed_id_for
from workload_graph.models import (
    BuildResult,
    EdgeType,
    ExternalRef,
    ProbeEntry,
    ProbeResult,
    Workspace,
)
from workload_graph.ruleset import Ruleset
from workload_graph.store.memory import InMemoryGraphStore

SM = EdgeType.STACK_MEMBER
S1, S2 = gk("s1", kind="stack"), gk("s2", kind="stack")
LB, VPC = gk("lb", kind="loadbalancer"), gk("vpc-1", kind="vpc")
WS = Workspace(workspace_id="ws", customer="example-customer",
               approved_account_ids=[ACCT_A, ACCT_B])
PROBE = ProbeResult(entries=[
    ProbeEntry(account_id=ACCT_A, source="stacks", availability="available"),
    ProbeEntry(account_id=ACCT_B, source="cloudtrail", availability="denied"),
])


def _store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    resources = [gres(gk(n), vpc_id="vpc-1") for n in "abcd"] + [
        gres(S1, stack_name="s1", resource_type="AWS::CloudFormation::Stack"),
        gres(S2, stack_name="s2", resource_type="AWS::CloudFormation::Stack"),
        gres(LB, vpc_id="vpc-1"), gres(VPC, vpc_id="vpc-1", resource_type="AWS::EC2::VPC"),
        gres(gk("stray"), vpc_id="vpc-1"), gres(gk("iso")),
        gres(gk("conflicted"), tag_conflict="true"),
    ]
    edges = [gedge(gk("a"), S1, SM), gedge(gk("b"), S1, SM), gedge(gk("c"), S2, SM),
             gedge(gk("d"), S2, SM), gedge(gk("conflicted"), S1, SM),
             gedge(LB, gk("a"), EdgeType.LB_TARGET), gedge(LB, gk("c"), EdgeType.LB_TARGET),
             gedge(gk("stray"), VPC, EdgeType.SAME_VPC),
             gedge(gk("a"), gk("ext", ACCT_C, "vpc"), EdgeType.CROSS_ACCOUNT_REF)]
    edges += [gedge(gk(n), VPC, EdgeType.SAME_VPC) for n in "abcd"]
    for r in resources:
        store.add_resource(r)
    for e in edges:
        store.add_edge(e)
    return store


def _build() -> BuildResult:
    return build_workloads(_store(), Ruleset(), WS, PROBE, ["snap-2", "snap-1"],
                           notes=["normaliser note"])


def test_build_produces_one_workload_per_seed_with_stable_ids() -> None:
    result = _build()
    expected = sorted([seed_id_for([gk("a"), gk("b"), S1]), seed_id_for([gk("c"), gk("d"), S2])])
    assert [w.workload_id for w in result.workloads] == expected


def test_shared_hubs_are_reported_and_listed_as_dependencies() -> None:
    result = _build()
    shared = {s.key: s.touches for s in result.shared_infrastructure}
    assert set(shared) == {LB, VPC}
    for workload in result.workloads:
        assert LB in workload.shared_dependencies
        assert workload.workload_id in shared[LB]


def test_cross_account_refs_are_external_refs_not_members() -> None:
    result = _build()
    w1 = next(w for w in result.workloads if gk("a") in [m.key for m in w.members])
    assert w1.external_refs == [ExternalRef(account_id=ACCT_C, reason="out_of_scope",
                                            arn=gk("ext", ACCT_C, "vpc").arn)]
    assert all(m.key.account_id == ACCT_A for m in w1.members)
    assert [s.account_id for s in result.external_refs] == [ACCT_C]


def test_conflicted_resource_is_unplaced_with_reason() -> None:
    result = _build()
    assert [(u.key, u.reason) for u in result.unmapped if u.key == gk("conflicted")] == [
        (gk("conflicted"), "tag_conflict")]


def test_every_resource_is_placed_exactly_once() -> None:
    result = _build()
    placed = [m.key for w in result.workloads for m in w.members]
    placed += [s.key for s in result.shared_infrastructure]
    placed += [m.key for g in result.residual_groups for m in g.members]
    placed += [u.key for u in result.unmapped]
    assert sorted(placed) == sorted(r.key for r in _store().resources())


def test_notes_record_degradations_and_input_notes() -> None:
    result = _build()
    assert "cloudtrail denied in 222222222222" in result.notes
    assert "normaliser note" in result.notes
    assert result.notes == sorted(set(result.notes))


def test_result_carries_ruleset_identity_and_sorted_snapshot_ids() -> None:
    result = _build()
    assert result.ruleset_hash == Ruleset().content_hash()
    assert result.ruleset_version == Ruleset().version
    assert result.generated_from == ["snap-1", "snap-2"]


def test_declared_members_have_reasons_and_evidence() -> None:
    for workload in _build().workloads:
        for member in workload.members:
            assert member.membership == "declared"
            assert member.reasons == [EdgeType.STACK_MEMBER]
            assert member.evidence


def test_evidence_summary_counts_edges_by_type() -> None:
    w1 = next(w for w in _build().workloads if gk("a") in [m.key for m in w.members])
    assert w1.evidence_summary["STACK_MEMBER"] == 2
    assert w1.evidence_summary["LB_TARGET"] == 1
    assert w1.evidence_summary["CROSS_ACCOUNT_REF"] == 1


def test_confidence_and_name_hint_are_filled() -> None:
    names = sorted(w.name_hint for w in _build().workloads)
    assert names == ["s1", "s2"]
    assert all(w.confidence == "high" for w in _build().workloads)


def test_build_is_byte_identical_on_repeat() -> None:
    assert _build().to_json() == _build().to_json()


def test_empty_store_gives_empty_result() -> None:
    result = build_workloads(InMemoryGraphStore(), Ruleset(), WS, ProbeResult(), [])
    assert result.workloads == [] and result.unmapped == [] and result.residual_groups == []
