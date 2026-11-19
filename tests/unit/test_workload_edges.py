"""Per-workload edge lists are a stable view of the store."""

from __future__ import annotations

from wg_test_helpers import build_from_snapshots, scenario_snapshots, scenario_workspace


def test_each_workload_edge_starts_at_a_member_and_the_list_is_sorted() -> None:
    result, _store = build_from_snapshots(scenario_snapshots("s1"), scenario_workspace("s1"))
    assert result.workloads
    for workload in result.workloads:
        member_ids = {
            f"{member.key.account_id}|{member.key.region}|{member.key.arn}"
            for member in workload.members
        }
        assert workload.edges
        keys = [(edge.src.key_str(), edge.dst, edge.edge_type.value) for edge in workload.edges]
        assert keys == sorted(keys)
        assert all(edge.src.key_str() in member_ids for edge in workload.edges)
        assert all(edge.evidence for edge in workload.edges)
        assert any(edge.edge_type.value == "STACK_MEMBER" for edge in workload.edges)


def test_edge_lists_do_not_change_when_the_build_is_repeated() -> None:
    first, _ = build_from_snapshots(scenario_snapshots("s2"), scenario_workspace("s2"))
    second, _ = build_from_snapshots(scenario_snapshots("s2"), scenario_workspace("s2"))
    assert first.to_json() == second.to_json()


def test_bad_account_and_region_are_rejected_before_any_aws_call() -> None:
    from workload_graph.live import build_account

    try:
        build_account("nope", "ap-south-1")
    except ValueError as exc:
        assert "12 digits" in str(exc)
    else:
        raise AssertionError("expected a rejection")
    try:
        build_account("111111111111", "not-a-region")
    except ValueError as exc:
        assert "region" in str(exc)
    else:
        raise AssertionError("expected a rejection")
