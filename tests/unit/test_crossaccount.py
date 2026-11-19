"""Step 9: cross-account handling tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C

from workload_graph.graph.crossaccount import resolve_cross_account
from workload_graph.ids import ResourceKey
from workload_graph.models import Edge, EdgeType, Evidence, ExternalRef, Resource
from workload_graph.ruleset import Ruleset
from workload_graph.store.memory import InMemoryGraphStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ACCT_AWS = "444444444444"


def _vpc(account: str, vid: str) -> ResourceKey:
    return ResourceKey(account_id=account, region="eu-west-1",
                       arn=f"arn:aws:ec2:eu-west-1:{account}:vpc/{vid}")


def _ref(src: ResourceKey, dst: ResourceKey, n: str = "1") -> Edge:
    return Edge(src=src, dst=dst, edge_type=EdgeType.CROSS_ACCOUNT_REF,
                evidence=(Evidence(source="topology", observed_at=T0, raw_ref=f"s#{n}",
                                   detail="peering"),))


LOCAL = _vpc(ACCT_A, "vpc-a")


def _store(*edges: Edge, extra: list[ResourceKey] | None = None) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    store.add_resource(Resource(key=LOCAL, resource_type="AWS::EC2::VPC"))
    for key in extra or []:
        store.add_resource(Resource(key=key, resource_type="AWS::EC2::VPC"))
    for edge in edges:
        store.add_edge(edge)
    return store


def test_edge_to_approved_collected_account_resolves_to_real_edge() -> None:
    remote = _vpc(ACCT_B, "vpc-b")
    report = resolve_cross_account(_store(_ref(LOCAL, remote), extra=[remote]),
                                   [ACCT_A, ACCT_B], [])
    assert [e.dst for e in report.resolved] == [remote]
    assert report.pending == () and report.summaries == ()


def test_edge_to_approved_but_uncollected_account_stays_pending_and_is_reported() -> None:
    remote = _vpc(ACCT_B, "vpc-b")
    report = resolve_cross_account(_store(_ref(LOCAL, remote)), [ACCT_A, ACCT_B], [])
    assert len(report.pending) == 1
    assert report.pending[0].dst == remote
    assert "not collected" in report.pending[0].reason
    assert any("pending" in n for n in report.notes)


def test_edge_to_collected_account_with_unknown_resource_is_pending_not_found() -> None:
    remote = _vpc(ACCT_B, "vpc-missing")
    other = _vpc(ACCT_B, "vpc-other")
    report = resolve_cross_account(_store(_ref(LOCAL, remote), extra=[other]),
                                   [ACCT_A, ACCT_B], [])
    assert "not found" in report.pending[0].reason


def test_edge_to_unapproved_account_becomes_out_of_scope_external_ref_without_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import boto3

    def _forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no AWS access may be attempted")

    monkeypatch.setattr(boto3, "client", _forbidden)
    monkeypatch.setattr(boto3, "Session", _forbidden)
    remote = _vpc(ACCT_C, "vpc-c")
    report = resolve_cross_account(_store(_ref(LOCAL, remote)), [ACCT_A, ACCT_B], [])
    assert report.external == ((LOCAL, ExternalRef(account_id=ACCT_C, reason="out_of_scope",
                                                    arn=remote.arn)),)
    assert report.summaries[0].is_finding is True


def test_known_aws_service_account_is_aws_owned_and_not_a_finding() -> None:
    remote = _vpc(ACCT_AWS, "vpc-svc")
    report = resolve_cross_account(_store(_ref(LOCAL, remote)), [ACCT_A], [ACCT_AWS])
    assert report.external[0][1].reason == "aws_owned"
    assert report.summaries[0].is_finding is False
    assert report.findings() == []


def test_same_external_account_referenced_many_times_is_reported_once_with_count() -> None:
    edges = [_ref(LOCAL, _vpc(ACCT_C, f"vpc-{i}"), str(i)) for i in range(3)]
    report = resolve_cross_account(_store(*edges), [ACCT_A], [])
    assert len(report.summaries) == 1
    assert report.summaries[0].account_id == ACCT_C
    assert report.summaries[0].reference_count == 3


def test_cross_account_edges_have_zero_weight_by_default() -> None:
    assert Ruleset().weight(EdgeType.CROSS_ACCOUNT_REF) == 0.0


def test_dependencies_for_members_lists_resolved_pending_and_external() -> None:
    b_present, b_pending, c = _vpc(ACCT_B, "b1"), _vpc(ACCT_B, "b2"), _vpc(ACCT_C, "c1")
    store = _store(_ref(LOCAL, b_present, "1"), _ref(LOCAL, b_pending, "2"),
                   _ref(LOCAL, c, "3"), extra=[b_present])
    report = resolve_cross_account(store, [ACCT_A, ACCT_B], [])
    deps, externals = report.for_members({LOCAL})
    assert deps == sorted([b_present, b_pending])
    assert externals == [ExternalRef(account_id=ACCT_C, reason="out_of_scope", arn=c.arn)]
    assert report.for_members({b_present}) == ([], [])


def test_existing_external_ref_destination_is_kept() -> None:
    edge = Edge(src=LOCAL, dst=ExternalRef(account_id=ACCT_C, reason="unknown"),
                edge_type=EdgeType.CROSS_ACCOUNT_REF,
                evidence=(Evidence(source="topology", observed_at=T0, raw_ref="s#x",
                                   detail="x"),))
    report = resolve_cross_account(_store(edge), [ACCT_A], [])
    assert report.external[0][1].reason == "unknown"


def test_non_cross_account_edges_are_ignored() -> None:
    edge = Edge(src=LOCAL, dst=_vpc(ACCT_A, "vpc-z"), edge_type=EdgeType.SAME_VPC,
                evidence=(Evidence(source="topology", observed_at=T0, raw_ref="s#y",
                                   detail="y"),))
    report = resolve_cross_account(_store(edge), [ACCT_A], [])
    assert report.resolved == () and report.pending == () and report.external == ()
