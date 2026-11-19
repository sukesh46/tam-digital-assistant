"""Step 5: collectors, tested offline with botocore.stub.Stubber."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import boto3
import pytest
from botocore.stub import ANY, Stubber
from wg_test_helpers import ACCT_A, FROZEN_NOW, REGION, ct_event

from workload_graph.collectors import COLLECTORS
from workload_graph.collectors.base import (
    FORBIDDEN_PREFIXES,
    CollectionScope,
    ForbiddenOperationError,
)
from workload_graph.collectors.cloudtrail import CloudTrailCollector
from workload_graph.collectors.stacks import StacksCollector
from workload_graph.collectors.tags import TagsCollector
from workload_graph.collectors.topology import TopologyCollector
from workload_graph.models import RawSnapshot

STACK_ID = (
    "arn:aws:cloudformation:eu-west-1:111111111111:stack/orders/00000000-0000-0000-0000-0000000000aa"
)
TG_ARN = "arn:aws:elasticloadbalancing:eu-west-1:111111111111:targetgroup/web/0000000000000001"
LB_ARN = "arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/web/0000000000000001"


class GuardedClient:
    """Wraps a boto3 client; any non-allowlisted or mutating operation is a violation."""

    def __init__(self, client: Any, allowed: frozenset[str], violations: list[str]) -> None:
        self._client = client
        self._allowed = allowed
        self._violations = violations
        self.meta = client.meta

    def __getattr__(self, name: str) -> Any:
        mapping: dict[str, str] = self._client.meta.method_to_api_mapping
        if name in mapping:
            op = mapping[name]
            if op not in self._allowed or op.startswith(FORBIDDEN_PREFIXES):
                self._violations.append(op)
                raise AssertionError(f"forbidden operation {op}")
        return getattr(self._client, name)


class StubbedFactory:
    """A client factory returning a Stubber-wrapped boto3 client per service (no network)."""

    def __init__(self) -> None:
        self.by_service: dict[str, Any] = {}
        self.stubbers: dict[str, Stubber] = {}
        self.guard: frozenset[str] | None = None
        self.violations: list[str] = []
        self.calls: list[tuple[str, str, str]] = []

    def stubber(self, service: str) -> Stubber:
        if service not in self.stubbers:
            client = boto3.client(
                service, region_name=REGION,
                aws_access_key_id="testing-not-a-key", aws_secret_access_key="testing-not-a-secret",
            )
            self.by_service[service] = client
            self.stubbers[service] = Stubber(client)
            self.stubbers[service].activate()
        return self.stubbers[service]

    def __call__(self, service: str, account_id: str, region: str) -> Any:
        self.calls.append((service, account_id, region))
        self.stubber(service)
        client = self.by_service[service]
        if self.guard is not None:
            return GuardedClient(client, self.guard, self.violations)
        return client

    def assert_done(self) -> None:
        for stubber in self.stubbers.values():
            stubber.assert_no_pending_responses()


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _scope(**overrides: Any) -> CollectionScope:
    values: dict[str, Any] = {
        "now": FROZEN_NOW, "max_calls": 50, "seed_resource_ids": ("i-0seed1",),
        "creation_event_names": ("RunInstances",),
    }
    values.update(overrides)
    return CollectionScope(**values)


# --- happy-path stub setups per collector ---------------------------------------------------

def _stub_tags(f: StubbedFactory) -> None:
    s = f.stubber("resourcegroupstaggingapi")
    s.add_response("get_resources", {
        "ResourceTagMappingList": [{
            "ResourceARN": "arn:aws:ec2:eu-west-1:111111111111:instance/i-0a",
            "Tags": [{"Key": "app", "Value": "orders"}]}],
        "PaginationToken": ""}, {"ResourcesPerPage": ANY})


def _stub_stacks(f: StubbedFactory) -> None:
    s = f.stubber("cloudformation")
    s.add_response("list_stacks", {"StackSummaries": [{
        "StackId": STACK_ID, "StackName": "orders", "CreationTime": FROZEN_NOW,
        "StackStatus": "CREATE_COMPLETE"}]}, {})
    s.add_response("describe_stack_resources", {"StackResources": [{
        "StackName": "orders", "StackId": STACK_ID, "LogicalResourceId": "Web",
        "PhysicalResourceId": "i-0a", "ResourceType": "AWS::EC2::Instance",
        "Timestamp": FROZEN_NOW, "ResourceStatus": "CREATE_COMPLETE"}]}, {"StackName": STACK_ID})


def _stub_topology(f: StubbedFactory) -> None:
    ec2 = f.stubber("ec2")
    ec2.add_response("describe_vpcs", {"Vpcs": [{"VpcId": "vpc-0a", "OwnerId": ACCT_A}]}, {})
    ec2.add_response("describe_subnets", {"Subnets": [{"SubnetId": "subnet-0a",
                                                        "VpcId": "vpc-0a"}]}, {})
    ec2.add_response("describe_security_groups", {"SecurityGroups": []}, {})
    ec2.add_response("describe_instances", {"Reservations": []}, {})
    ec2.add_response("describe_vpc_peering_connections", {"VpcPeeringConnections": []}, {})
    ec2.add_response("describe_transit_gateway_attachments",
                     {"TransitGatewayAttachments": []}, {})
    elb = f.stubber("elbv2")
    elb.add_response("describe_load_balancers", {"LoadBalancers": [{
        "LoadBalancerArn": LB_ARN, "LoadBalancerName": "web", "VpcId": "vpc-0a"}]}, {})
    elb.add_response("describe_target_groups", {"TargetGroups": [{
        "TargetGroupArn": TG_ARN, "TargetGroupName": "web", "LoadBalancerArns": [LB_ARN]}]}, {})
    elb.add_response("describe_target_health", {"TargetHealthDescriptions": [{
        "Target": {"Id": "i-0a", "Port": 80}}]}, {"TargetGroupArn": TG_ARN})


def _stub_cloudtrail(f: StubbedFactory) -> None:
    s = f.stubber("cloudtrail")
    s.add_response("lookup_events", {"Events": [
        ct_event("e1", "RunInstances", "2026-01-10T10:00:00Z",
                 [("AWS::EC2::Instance", "i-0seed1")])]},
        {"LookupAttributes": [{"AttributeKey": "ResourceName", "AttributeValue": "i-0seed1"}],
         "StartTime": ANY, "EndTime": ANY})
    s.add_response("lookup_events", {"Events": [
        ct_event("e2", "RunInstances", "2026-01-10T10:10:00Z",
                 [("AWS::EC2::Instance", "i-0new")]),
        ct_event("e3", "RunInstances", "2026-01-10T10:12:00Z",
                 [("AWS::EC2::Instance", "i-0other")],
                 principal="arn:aws:sts::111111111111:assumed-role/someone/else")]},
        {"LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": "RunInstances"}],
         "StartTime": ANY, "EndTime": ANY})


HAPPY: dict[str, Callable[[StubbedFactory], None]] = {
    "tags": _stub_tags, "stacks": _stub_stacks, "topology": _stub_topology,
    "cloudtrail": _stub_cloudtrail,
}
SERVICES: dict[str, list[str]] = {
    "tags": ["resourcegroupstaggingapi"], "stacks": ["cloudformation"],
    "topology": ["ec2", "elbv2"], "cloudtrail": ["cloudtrail"],
}
FIRST_METHOD: dict[str, list[tuple[str, str]]] = {
    "tags": [("resourcegroupstaggingapi", "get_resources")],
    "stacks": [("cloudformation", "list_stacks")],
    "topology": [("ec2", m) for m in (
        "describe_vpcs", "describe_subnets", "describe_security_groups", "describe_instances",
        "describe_vpc_peering_connections", "describe_transit_gateway_attachments")]
    + [("elbv2", "describe_load_balancers"), ("elbv2", "describe_target_groups")],
    "cloudtrail": [("cloudtrail", "lookup_events")],
}


def _collect(source: str, f: StubbedFactory, scope: CollectionScope | None = None,
             sleep: SleepRecorder | None = None) -> RawSnapshot:
    collector = COLLECTORS[source](sleep=sleep or SleepRecorder())
    return collector.collect(f, ACCT_A, REGION, scope or _scope())


# --- tests -----------------------------------------------------------------------------------

@pytest.mark.parametrize("source", sorted(HAPPY))
def test_single_page_responses_are_recorded(source: str) -> None:
    f = StubbedFactory()
    HAPPY[source](f)
    snap = _collect(source, f)
    f.assert_done()
    assert snap.status == "ok"
    assert snap.source == source
    assert snap.account_id == ACCT_A and snap.region == REGION
    assert snap.collected_at == FROZEN_NOW
    assert snap.has_data()
    assert all("ResponseMetadata" not in p for pages in snap.payload.values() for p in pages)


@pytest.mark.parametrize("source", sorted(HAPPY))
def test_operations_called_lists_every_operation_invoked(source: str) -> None:
    f = StubbedFactory()
    HAPPY[source](f)
    snap = _collect(source, f)
    expected = {
        "tags": ["GetResources"],
        "stacks": ["ListStacks", "DescribeStackResources"],
        "topology": ["DescribeVpcs", "DescribeSubnets", "DescribeSecurityGroups",
                     "DescribeInstances", "DescribeVpcPeeringConnections",
                     "DescribeTransitGatewayAttachments", "DescribeLoadBalancers",
                     "DescribeTargetGroups", "DescribeTargetHealth"],
        "cloudtrail": ["LookupEvents", "LookupEvents"],
    }[source]
    assert snap.operations_called == expected


@pytest.mark.parametrize("source", sorted(HAPPY))
def test_collectors_only_call_allowlisted_read_only_operations(source: str) -> None:
    collector_cls = COLLECTORS[source]
    f = StubbedFactory()
    f.guard = collector_cls.allowed_operations
    HAPPY[source](f)
    snap = _collect(source, f)
    assert f.violations == []
    assert snap.status == "ok"
    assert set(snap.operations_called) <= collector_cls.allowed_operations


@pytest.mark.parametrize("source", sorted(COLLECTORS))
def test_allowlists_contain_no_mutating_operations(source: str) -> None:
    allowed = COLLECTORS[source].allowed_operations
    assert allowed
    assert not [op for op in allowed if op.startswith(FORBIDDEN_PREFIXES)]
    assert all(op.startswith(("Describe", "List", "Get", "Lookup")) for op in allowed)


def test_guard_catches_a_mutating_call() -> None:
    f = StubbedFactory()
    f.guard = TopologyCollector.allowed_operations
    client = f("ec2", ACCT_A, REGION)
    with pytest.raises(AssertionError):
        client.run_instances(MinCount=1, MaxCount=1)
    assert f.violations == ["RunInstances"]


def test_recorder_refuses_operations_outside_the_allowlist() -> None:
    from workload_graph.collectors.base import CallRecorder

    f = StubbedFactory()
    recorder = CallRecorder(_scope(), SleepRecorder(), frozenset({"DescribeVpcs"}))
    with pytest.raises(ForbiddenOperationError):
        recorder.call(f("ec2", ACCT_A, REGION), "TerminateInstances", InstanceIds=["i-1"])


def test_multi_page_tags_response_is_handled() -> None:
    f = StubbedFactory()
    s = f.stubber("resourcegroupstaggingapi")
    s.add_response("get_resources", {"ResourceTagMappingList": [
        {"ResourceARN": "arn:aws:s3:::a", "Tags": []}], "PaginationToken": "t1"})
    s.add_response("get_resources", {"ResourceTagMappingList": [
        {"ResourceARN": "arn:aws:s3:::b", "Tags": []}], "PaginationToken": ""},
        {"ResourcesPerPage": ANY, "PaginationToken": "t1"})
    snap = _collect("tags", f)
    f.assert_done()
    assert len(snap.payload["GetResources"]) == 2
    assert snap.operations_called == ["GetResources", "GetResources"]
    assert snap.status == "ok"


def test_multi_page_stacks_response_is_handled() -> None:
    f = StubbedFactory()
    s = f.stubber("cloudformation")
    s.add_response("list_stacks", {"StackSummaries": [], "NextToken": "n1"}, {})
    s.add_response("list_stacks", {"StackSummaries": []}, {"NextToken": "n1"})
    snap = _collect("stacks", f)
    f.assert_done()
    assert snap.operations_called == ["ListStacks", "ListStacks"]
    assert snap.status == "empty"


def test_multi_page_topology_and_cloudtrail_responses_are_handled() -> None:
    f = StubbedFactory()
    ec2 = f.stubber("ec2")
    ec2.add_response("describe_vpcs", {"Vpcs": [{"VpcId": "vpc-1"}], "NextToken": "v2"}, {})
    ec2.add_response("describe_vpcs", {"Vpcs": [{"VpcId": "vpc-2"}]}, {"NextToken": "v2"})
    for method, key in (("describe_subnets", "Subnets"),
                        ("describe_security_groups", "SecurityGroups"),
                        ("describe_instances", "Reservations"),
                        ("describe_vpc_peering_connections", "VpcPeeringConnections"),
                        ("describe_transit_gateway_attachments", "TransitGatewayAttachments")):
        ec2.add_response(method, {key: []}, {})
    elb = f.stubber("elbv2")
    elb.add_response("describe_load_balancers", {"LoadBalancers": [], "NextMarker": "m"}, {})
    elb.add_response("describe_load_balancers", {"LoadBalancers": []}, {"Marker": "m"})
    elb.add_response("describe_target_groups", {"TargetGroups": []}, {})
    snap = _collect("topology", f)
    f.assert_done()
    assert len(snap.payload["DescribeVpcs"]) == 2
    assert snap.operations_called.count("DescribeLoadBalancers") == 2

    f2 = StubbedFactory()
    ct = f2.stubber("cloudtrail")
    ct.add_response("lookup_events", {"Events": [], "NextToken": "c2"})
    ct.add_response("lookup_events", {"Events": []},
                    {"LookupAttributes": ANY, "StartTime": ANY, "EndTime": ANY,
                     "NextToken": "c2"})
    snap2 = _collect("cloudtrail", f2)
    f2.assert_done()
    assert snap2.operations_called == ["LookupEvents", "LookupEvents"]
    assert snap2.status == "empty"


@pytest.mark.parametrize("source", sorted(FIRST_METHOD))
def test_access_denied_produces_status_denied_not_an_exception(source: str) -> None:
    f = StubbedFactory()
    for service, method in FIRST_METHOD[source]:
        f.stubber(service).add_client_error(method, service_error_code="AccessDenied",
                                            service_message="denied", http_status_code=403)
    snap = _collect(source, f)
    assert snap.status == "denied"
    assert any("AccessDenied" in n for n in snap.notes)


def test_partial_denial_gives_partial_status() -> None:
    f = StubbedFactory()
    ec2 = f.stubber("ec2")
    ec2.add_response("describe_vpcs", {"Vpcs": [{"VpcId": "vpc-1"}]}, {})
    ec2.add_client_error("describe_subnets", service_error_code="UnauthorizedOperation",
                         http_status_code=403)
    for method, key in (("describe_security_groups", "SecurityGroups"),
                        ("describe_instances", "Reservations"),
                        ("describe_vpc_peering_connections", "VpcPeeringConnections"),
                        ("describe_transit_gateway_attachments", "TransitGatewayAttachments")):
        ec2.add_response(method, {key: []}, {})
    elb = f.stubber("elbv2")
    elb.add_response("describe_load_balancers", {"LoadBalancers": []}, {})
    elb.add_response("describe_target_groups", {"TargetGroups": []}, {})
    snap = _collect("topology", f)
    assert snap.status == "partial"
    assert any("DescribeSubnets" in n for n in snap.notes)


def test_throttling_is_retried_with_exponential_backoff_then_succeeds() -> None:
    f = StubbedFactory()
    s = f.stubber("resourcegroupstaggingapi")
    s.add_client_error("get_resources", service_error_code="ThrottlingException",
                       http_status_code=400)
    s.add_client_error("get_resources", service_error_code="Throttling", http_status_code=400)
    _stub_tags(f)
    sleep = SleepRecorder()
    snap = _collect("tags", f, sleep=sleep)
    assert snap.status == "ok"
    assert sleep.delays == [0.5, 1.0]
    assert snap.operations_called == ["GetResources"] * 3


def test_persistent_throttling_gives_error_with_note() -> None:
    f = StubbedFactory()
    s = f.stubber("resourcegroupstaggingapi")
    for _ in range(5):
        s.add_client_error("get_resources", service_error_code="ThrottlingException",
                           http_status_code=400)
    sleep = SleepRecorder()
    snap = _collect("tags", f, sleep=sleep)
    assert snap.status == "error"
    assert sleep.delays == [0.5, 1.0, 2.0, 4.0]
    assert any("throttl" in n.lower() for n in snap.notes)


def test_persistent_throttling_after_some_pages_gives_partial() -> None:
    f = StubbedFactory()
    s = f.stubber("resourcegroupstaggingapi")
    s.add_response("get_resources", {"ResourceTagMappingList": [
        {"ResourceARN": "arn:aws:s3:::a", "Tags": []}], "PaginationToken": "t1"})
    for _ in range(5):
        s.add_client_error("get_resources", service_error_code="ThrottlingException",
                           http_status_code=400)
    snap = _collect("tags", f)
    assert snap.status == "partial"


def test_call_budget_is_enforced_and_yields_partial() -> None:
    f = StubbedFactory()
    s = f.stubber("resourcegroupstaggingapi")
    s.add_response("get_resources", {"ResourceTagMappingList": [
        {"ResourceARN": "arn:aws:s3:::a", "Tags": []}], "PaginationToken": "t1"})
    snap = _collect("tags", f, scope=_scope(max_calls=1))
    assert snap.status == "partial"
    assert snap.operations_called == ["GetResources"]
    assert any("budget" in n for n in snap.notes)


def test_cloudtrail_budget_is_shared_between_passes() -> None:
    f = StubbedFactory()
    s = f.stubber("cloudtrail")
    for seed in ("i-1", "i-2"):
        s.add_response("lookup_events", {"Events": [
            ct_event(f"e-{seed}", "RunInstances", "2026-01-10T10:00:00Z",
                     [("AWS::EC2::Instance", seed)])]})
    snap = _collect("cloudtrail", f, scope=_scope(max_calls=2,
                                                   seed_resource_ids=("i-1", "i-2", "i-3")))
    assert snap.status == "partial"
    assert snap.operations_called == ["LookupEvents", "LookupEvents"]
    assert any("budget" in n for n in snap.notes)


def test_cloudtrail_pass_two_keeps_only_principals_seen_in_pass_one() -> None:
    f = StubbedFactory()
    _stub_cloudtrail(f)
    snap = _collect("cloudtrail", f)
    events = [e for p in snap.payload["LookupEvents"] for e in p["Events"]]
    names = {e["Resources"][0]["ResourceName"] for e in events}
    assert names == {"i-0seed1", "i-0new"}
    assert snap.payload["_seeds"] == [{"ResourceIds": ["i-0seed1"]}]


def test_cloudtrail_pass_two_window_surrounds_creation_time() -> None:
    f = StubbedFactory()
    s = f.stubber("cloudtrail")
    s.add_response("lookup_events", {"Events": [
        ct_event("e1", "RunInstances", "2026-01-10T10:00:00Z",
                 [("AWS::EC2::Instance", "i-0seed1")])]})
    from datetime import UTC, datetime
    s.add_response("lookup_events", {"Events": []}, {
        "LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": "RunInstances"}],
        "StartTime": datetime(2026, 1, 10, 9, 30, tzinfo=UTC),
        "EndTime": datetime(2026, 1, 10, 10, 30, tzinfo=UTC)})
    snap = _collect("cloudtrail", f)
    f.assert_done()
    assert snap.status == "ok"


def test_snapshot_round_trips_through_json_after_collection() -> None:
    f = StubbedFactory()
    _stub_stacks(f)
    snap = _collect("stacks", f)
    restored = RawSnapshot.model_validate_json(snap.model_dump_json())
    assert json.loads(restored.model_dump_json()) == json.loads(snap.model_dump_json())


def test_client_factory_receives_account_and_region() -> None:
    f = StubbedFactory()
    _stub_tags(f)
    _collect("tags", f)
    assert f.calls == [("resourcegroupstaggingapi", ACCT_A, REGION)]


def test_unexpected_client_error_gives_error_status_with_code_only() -> None:
    f = StubbedFactory()
    f.stubber("cloudformation").add_client_error(
        "list_stacks", service_error_code="ValidationError",
        service_message="secret-ish message", http_status_code=400)
    snap = _collect("stacks", f)
    assert snap.status == "error"
    assert any("ValidationError" in n for n in snap.notes)
    assert not any("secret-ish" in n for n in snap.notes)


def test_scope_from_ruleset_copies_cloudtrail_settings() -> None:
    from workload_graph.ruleset import Ruleset

    scope = CollectionScope.from_ruleset(Ruleset(), now=FROZEN_NOW, seeds=["b", "a", "a"])
    assert scope.max_calls == 200
    assert scope.seed_resource_ids == ("a", "b")
    assert "RunInstances" in scope.creation_event_names


def test_registry_lists_all_four_collectors() -> None:
    assert sorted(COLLECTORS) == ["cloudtrail", "stacks", "tags", "topology"]
    assert COLLECTORS["tags"] is TagsCollector
    assert COLLECTORS["stacks"] is StacksCollector
    assert COLLECTORS["cloudtrail"] is CloudTrailCollector
