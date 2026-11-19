"""Step 4: stacks normaliser tests."""

from __future__ import annotations

from wg_test_helpers import ACCT_A, REGION, load_snapshot, make_snapshot

from workload_graph.ids import ResourceKey
from workload_graph.models import EdgeType
from workload_graph.normalisers.base import NormalisedBatch
from workload_graph.normalisers.stacks import normalise_stacks
from workload_graph.ruleset import Ruleset

STACK_PREFIX = "arn:aws:cloudformation:eu-west-1:111111111111:stack/"
ORDERS = ResourceKey(account_id=ACCT_A, region=REGION,
                     arn=STACK_PREFIX + "orders/00000000-0000-0000-0000-000000000001")
OLD = ResourceKey(account_id=ACCT_A, region=REGION,
                  arn=STACK_PREFIX + "old/00000000-0000-0000-0000-000000000002")
PARENT = ResourceKey(account_id=ACCT_A, region=REGION,
                     arn=STACK_PREFIX + "platform/00000000-0000-0000-0000-000000000003")
CHILD = ResourceKey(account_id=ACCT_A, region=REGION,
                    arn=STACK_PREFIX + "platform-Network-ABC/00000000-0000-0000-0000-000000000004")
INSTANCE = ResourceKey(account_id=ACCT_A, region=REGION,
                       arn="arn:aws:ec2:eu-west-1:111111111111:instance/i-0orders1")
VPC = ResourceKey(account_id=ACCT_A, region=REGION,
                  arn="arn:aws:ec2:eu-west-1:111111111111:vpc/vpc-0platform")


def _batch() -> NormalisedBatch:
    return normalise_stacks(load_snapshot("unit/111111111111/stacks.json"), Ruleset())


def _members_of(batch: NormalisedBatch, stack: ResourceKey) -> set[str]:
    return {
        e.src.arn for e in batch.edges
        if e.edge_type is EdgeType.STACK_MEMBER and e.dst == stack
    }


def test_stack_member_edges_are_created_for_every_stack_resource() -> None:
    members = _members_of(_batch(), ORDERS)
    assert members == {
        "arn:aws:ec2:eu-west-1:111111111111:instance/i-0orders1",
        "arn:aws:rds:eu-west-1:111111111111:db:orders-db",
        "arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/orders-alb/0000000000000001",
        "unresolved:Custom::Thing:custom-123",
        "arn:aws:s3:::orders-assets",
    }


def test_deleted_stacks_are_ignored() -> None:
    batch = _batch()
    keys = {r.key for r in batch.resources}
    assert OLD not in keys
    assert all("i-0old" not in r.key.arn for r in batch.resources)


def test_live_stacks_are_emitted_as_resources_with_their_name() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert resources[ORDERS].resource_type == "AWS::CloudFormation::Stack"
    assert resources[ORDERS].name == "orders"


def test_nested_stack_records_parent_link_without_merging_into_parent() -> None:
    batch = _batch()
    resources = {r.key: r for r in batch.resources}
    assert resources[CHILD].attributes["parent_stack_id"] == PARENT.arn
    assert CHILD.arn not in _members_of(batch, PARENT)
    assert any("nested" in n and "platform-Network-ABC" in n for n in batch.notes)


def test_nested_stack_resources_belong_to_the_child_stack_group() -> None:
    batch = _batch()
    assert VPC.arn in _members_of(batch, CHILD)
    assert VPC.arn not in _members_of(batch, PARENT)


def test_physical_ids_are_converted_with_arn_for() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert INSTANCE in resources
    assert resources[INSTANCE].resource_type == "AWS::EC2::Instance"


def test_unknown_types_get_unresolved_key_and_note() -> None:
    batch = _batch()
    unresolved = [r for r in batch.resources if r.key.arn.startswith("unresolved:")]
    assert [r.key.arn for r in unresolved] == ["unresolved:Custom::Thing:custom-123"]
    assert unresolved[0].resource_type == "Custom::Thing"
    assert any("Custom::Thing" in n for n in batch.notes)


def test_members_carry_stack_name_attribute() -> None:
    resources = {r.key: r for r in _batch().resources}
    assert resources[INSTANCE].attributes["stack_name"] == "orders"
    assert resources[INSTANCE].attributes["logical_id"] == "WebInstance"


def test_stack_resource_without_physical_id_is_skipped_with_note() -> None:
    stack_id = STACK_PREFIX + "x/00000000-0000-0000-0000-000000000009"
    snap = make_snapshot("stacks", {
        "ListStacks": [{"StackSummaries": [{
            "StackId": stack_id, "StackName": "x", "CreationTime": "2025-01-01T00:00:00Z",
            "StackStatus": "CREATE_FAILED"}]}],
        "DescribeStackResources": [{"StackResources": [{
            "StackName": "x", "StackId": stack_id, "LogicalResourceId": "Broken",
            "ResourceType": "AWS::EC2::Instance", "Timestamp": "2025-01-01T00:00:00Z",
            "ResourceStatus": "CREATE_FAILED"}]}],
    })
    batch = normalise_stacks(snap, Ruleset())
    assert [e for e in batch.edges] == []
    assert any("Broken" in n for n in batch.notes)


def test_empty_snapshot_gives_empty_batch() -> None:
    batch = normalise_stacks(make_snapshot("stacks", {"ListStacks": [{"StackSummaries": []}]}),
                             Ruleset())
    assert batch.resources == () and batch.edges == ()
