"""Step 4: topology normaliser tests."""

from __future__ import annotations

from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C, REGION, load_snapshot

from workload_graph.ids import ResourceKey
from workload_graph.models import Edge, EdgeType
from workload_graph.normalisers.base import NormalisedBatch
from workload_graph.normalisers.topology import normalise_topology
from workload_graph.ruleset import Ruleset


def _ec2(kind: str, rid: str, account: str = ACCT_A) -> ResourceKey:
    return ResourceKey(account_id=account, region=REGION,
                       arn=f"arn:aws:ec2:{REGION}:{account}:{kind}/{rid}")


VPC = _ec2("vpc", "vpc-0main")
SUBNET_A = _ec2("subnet", "subnet-0a")
SUBNET_B = _ec2("subnet", "subnet-0b")
SG_WEB = _ec2("security-group", "sg-0web")
WEB1 = _ec2("instance", "i-0web1")
WEB2 = _ec2("instance", "i-0web2")
LB = ResourceKey(account_id=ACCT_A, region=REGION, arn=(
    "arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/web/0000000000000001"))
TG = ResourceKey(account_id=ACCT_A, region=REGION, arn=(
    "arn:aws:elasticloadbalancing:eu-west-1:111111111111:targetgroup/web-tg/0000000000000001"))


def _batch() -> NormalisedBatch:
    return normalise_topology(load_snapshot("unit/111111111111/topology.json"), Ruleset())


def _edges(edge_type: EdgeType) -> set[tuple[ResourceKey, object]]:
    return {(e.src, e.dst) for e in _batch().edges if e.edge_type is edge_type}


def test_resources_link_to_their_subnet_and_vpc() -> None:
    assert (WEB1, SUBNET_A) in _edges(EdgeType.SAME_SUBNET)
    assert (WEB2, SUBNET_B) in _edges(EdgeType.SAME_SUBNET)
    assert (WEB1, VPC) in _edges(EdgeType.SAME_VPC)
    assert (SUBNET_A, VPC) in _edges(EdgeType.SAME_VPC)


def test_no_pairwise_edges_between_members_of_the_same_container() -> None:
    for _src, dst in _edges(EdgeType.SAME_SUBNET) | _edges(EdgeType.SAME_VPC):
        assert isinstance(dst, ResourceKey)
        assert ":instance/" not in dst.arn


def test_containers_are_emitted_as_resources_with_vpc_attribute() -> None:
    resources = {r.key: r for r in _batch().resources}
    for key in (VPC, SUBNET_A, SUBNET_B, SG_WEB, WEB1, LB, TG):
        assert resources[key].attributes["vpc_id"] == "vpc-0main"
    assert resources[SUBNET_A].resource_type == "AWS::EC2::Subnet"
    assert resources[WEB1].name == "web-1"


def test_load_balancer_target_groups_produce_lb_target_edges_to_resolvable_targets() -> None:
    lb_edges = _edges(EdgeType.LB_TARGET)
    assert (LB, WEB1) in lb_edges
    assert (LB, WEB2) in lb_edges
    assert (TG, WEB1) in lb_edges


def test_ip_targets_are_recorded_as_unresolved_notes_not_guessed() -> None:
    batch = _batch()
    assert any("10.0.1.25" in n and "unresolved" in n for n in batch.notes)
    assert all("10.0.1.25" not in e.dst_str() for e in batch.edges)


def test_shared_security_groups_produce_edges_from_each_user() -> None:
    sg_edges = _edges(EdgeType.SHARED_SECURITY_GROUP)
    assert {(WEB1, SG_WEB), (WEB2, SG_WEB), (LB, SG_WEB)} <= sg_edges


def test_vpc_peering_with_different_owner_emits_cross_account_ref() -> None:
    cross = _edges(EdgeType.CROSS_ACCOUNT_REF)
    assert (VPC, _ec2("vpc", "vpc-0peer", ACCT_B)) in cross


def test_same_owner_or_deleted_peering_emits_no_cross_account_ref() -> None:
    cross = {str(dst) for _, dst in _edges(EdgeType.CROSS_ACCOUNT_REF)}
    assert not any("vpc-0other" in d for d in cross)
    assert not any("vpc-0gone" in d for d in cross)


def test_transit_gateway_attachment_with_different_owner_emits_cross_account_ref() -> None:
    cross = _edges(EdgeType.CROSS_ACCOUNT_REF)
    assert (VPC, _ec2("transit-gateway", "tgw-0shared", ACCT_C)) in cross
    local_tgw = _ec2("transit-gateway", "tgw-0local")
    assert (local_tgw, _ec2("vpc", "vpc-0remote", ACCT_B)) in cross
    assert local_tgw in {r.key for r in _batch().resources}


def test_terminated_instances_are_skipped_with_note() -> None:
    batch = _batch()
    assert all("i-0term" not in r.key.arn for r in batch.resources)
    assert any("i-0term" in n for n in batch.notes)


def test_every_edge_has_topology_evidence() -> None:
    edges: tuple[Edge, ...] = _batch().edges
    assert edges
    assert all(e.evidence and e.evidence[0].source == "topology" for e in edges)
