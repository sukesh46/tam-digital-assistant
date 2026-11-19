"""Step 2: domain model tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from workload_graph.ids import ResourceKey
from workload_graph.models import (
    BuildResult,
    CandidateWorkload,
    Edge,
    EdgeType,
    Evidence,
    ExternalRef,
    ExternalRefSummary,
    Member,
    ProbeEntry,
    ProbeResult,
    RawSnapshot,
    ResidualGroup,
    Resource,
    SharedInfra,
    Unplaced,
    Workspace,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
K1 = ResourceKey(account_id="111111111111", region="eu-west-1", arn="arn:aws:ec2:a")
K2 = ResourceKey(account_id="111111111111", region="eu-west-1", arn="arn:aws:ec2:b")


def _evidence(detail: str = "d") -> Evidence:
    return Evidence(source="stacks", observed_at=T0, raw_ref="snap-1#/payload/X/0", detail=detail)


def test_evidence_requires_timezone_aware_datetime() -> None:
    with pytest.raises(ValidationError):
        Evidence(source="tags", observed_at=datetime(2026, 1, 1), raw_ref="s#/", detail="x")  # noqa: DTZ001


def test_evidence_normalises_to_utc() -> None:
    ev = Evidence(
        source="tags",
        observed_at=datetime(2026, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        raw_ref="s#/",
        detail="x",
    )
    assert ev.observed_at == T0
    assert ev.observed_at.utcoffset() == timedelta(0)


def test_evidence_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        Evidence(source="config", observed_at=T0, raw_ref="s#/", detail="x")  # type: ignore[arg-type]


def test_evidence_rejects_long_detail() -> None:
    with pytest.raises(ValidationError):
        Evidence(source="tags", observed_at=T0, raw_ref="s#/", detail="x" * 1000)


def test_edge_rejects_empty_evidence() -> None:
    with pytest.raises(ValidationError):
        Edge(src=K1, dst=K2, edge_type=EdgeType.SAME_VPC, evidence=())


def test_edge_accepts_external_ref_destination() -> None:
    edge = Edge(
        src=K1,
        dst=ExternalRef(account_id="333333333333", reason="out_of_scope"),
        edge_type=EdgeType.CROSS_ACCOUNT_REF,
        evidence=(_evidence(),),
    )
    assert isinstance(edge.dst, ExternalRef)


def test_external_ref_rejects_unknown_reason() -> None:
    with pytest.raises(ValidationError):
        ExternalRef(account_id="333333333333", reason="because")  # type: ignore[arg-type]


def test_external_ref_is_hashable_and_sortable_by_sort_key() -> None:
    a = ExternalRef(account_id="333333333333", reason="out_of_scope")
    b = ExternalRef(account_id="333333333333", reason="out_of_scope")
    assert hash(a) == hash(b)
    assert a.sort_key() == b.sort_key()


def test_frozen_models_raise_on_mutation() -> None:
    ev = _evidence()
    with pytest.raises(ValidationError):
        ev.detail = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        K1.arn = "x"  # type: ignore[misc]


def test_resource_sources_serialise_sorted() -> None:
    r = Resource(key=K1, resource_type="AWS::EC2::Instance", sources=frozenset({"tags", "stacks"}))
    dumped = r.model_dump(mode="json")
    assert dumped["sources"] == ["stacks", "tags"]


def _samples() -> list[BaseModel]:
    ev = _evidence()
    member = Member(key=K1, membership="declared", affinity=None,
                    reasons=[EdgeType.STACK_MEMBER], evidence=[ev])
    wl = CandidateWorkload(
        workload_id="abcdef012345",
        name_hint="orders",
        members=[member],
        confidence="high",
        accounts=["111111111111"],
        sources_used=["stacks"],
        shared_dependencies=[K2],
        external_refs=[ExternalRef(account_id="333333333333", reason="out_of_scope")],
        evidence_summary={"STACK_MEMBER": 1},
    )
    probe = ProbeResult(entries=[ProbeEntry(account_id="111111111111", source="tags",
                                            availability="available")])
    return [
        K1,
        ev,
        Resource(key=K1, resource_type="AWS::EC2::Instance", name="web", tags={"app": "x"},
                 attributes={"vpc_id": "vpc-1"}, sources=frozenset({"tags"})),
        ExternalRef(account_id="333333333333", reason="aws_owned", arn="arn:aws:iam::x"),
        Edge(src=K1, dst=K2, edge_type=EdgeType.SAME_SUBNET, evidence=(ev,)),
        Edge(src=K1, dst=ExternalRef(account_id="333333333333", reason="unknown"),
             edge_type=EdgeType.CROSS_ACCOUNT_REF, evidence=(ev,)),
        RawSnapshot(snapshot_id="s1", source="tags", account_id="111111111111",
                    region="eu-west-1", collected_at=T0, operations_called=["GetResources"],
                    payload={"GetResources": [{"ResourceTagMappingList": []}]}, status="empty"),
        probe,
        member,
        wl,
        SharedInfra(key=K2, touches=["abcdef012345", "bbbbbbbbbbbb"]),
        ResidualGroup(account_id="111111111111", region="eu-west-1", vpc_id="vpc-1",
                      members=[Unplaced(key=K2, reason="below_threshold")]),
        ExternalRefSummary(account_id="333333333333", reason="out_of_scope",
                           reference_count=2, is_finding=True),
        Workspace(workspace_id="ws-1", customer="example-customer",
                  approved_account_ids=["111111111111"], regions=["eu-west-1"]),
        BuildResult(
            ruleset_version="0.1.0", ruleset_hash="h", workloads=[wl],
            shared_infrastructure=[], residual_groups=[], unmapped=[], external_refs=[],
            probe=probe, notes=["n"], generated_from=["s1"],
        ),
    ]


@pytest.mark.parametrize("model", _samples(), ids=lambda m: type(m).__name__)
def test_every_model_round_trips_through_json(model: BaseModel) -> None:
    text = model.model_dump_json()
    restored = type(model).model_validate_json(text)
    assert restored == model


def test_residual_group_confidence_is_always_low() -> None:
    with pytest.raises(ValidationError):
        ResidualGroup(account_id="111111111111", region="r", vpc_id="v", members=[],
                      confidence="high")  # type: ignore[arg-type]


def test_build_result_serialises_with_sorted_keys() -> None:
    result = _samples()[-1]
    assert isinstance(result, BuildResult)
    text = result.to_json()
    data = json.loads(text)
    assert list(data.keys()) == sorted(data.keys())
    assert text == json.dumps(data, sort_keys=True, indent=2) + "\n"


def test_workspace_rejects_bad_account_ids() -> None:
    with pytest.raises(ValidationError):
        Workspace(workspace_id="w", customer="example-customer", approved_account_ids=["1"])


def test_snapshot_has_data_ignores_metadata_keys() -> None:
    snap = RawSnapshot(snapshot_id="s", source="cloudtrail", account_id="111111111111",
                       region="eu-west-1", collected_at=T0, operations_called=[],
                       payload={"_seeds": [{"ResourceIds": ["i-1"]}],
                                "LookupEvents": [{"Events": []}]}, status="ok")
    assert snap.has_data() is False
