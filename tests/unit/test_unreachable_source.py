"""An endpoint that cannot be reached must cost one failed call, not one per seed.

Offline. The fake service objects raise botocore's connection error; no socket is opened.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

from workload_graph.collectors.base import (
    CallRecorder,
    CollectionScope,
    ConnectionFailedError,
)
from workload_graph.collectors.cloudtrail import CloudTrailCollector
from workload_graph.credentials import CLIENT_CONFIG, ambient_client
from workload_graph.live import collect_snapshots
from workload_graph.ruleset import Ruleset

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
OPERATIONS = {
    "get_resources": "GetResources", "list_stacks": "ListStacks",
    "describe_stack_resources": "DescribeStackResources", "describe_vpcs": "DescribeVpcs",
    "describe_subnets": "DescribeSubnets", "describe_security_groups": "DescribeSecurityGroups",
    "describe_instances": "DescribeInstances",
    "describe_vpc_peering_connections": "DescribeVpcPeeringConnections",
    "describe_transit_gateway_attachments": "DescribeTransitGatewayAttachments",
    "describe_load_balancers": "DescribeLoadBalancers",
    "describe_target_groups": "DescribeTargetGroups",
    "describe_target_health": "DescribeTargetHealth", "lookup_events": "LookupEvents",
}


class _Meta:
    def __init__(self, endpoint: str) -> None:
        self.method_to_api_mapping = OPERATIONS
        self.endpoint_url = endpoint


class DownClient:
    """Every call fails the way an unresolvable host does."""

    def __init__(self, endpoint: str = "https://cloudtrail.example") -> None:
        self.meta = _Meta(endpoint)
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        if name not in OPERATIONS:
            raise AttributeError(name)

        def call(**_kwargs: Any) -> Any:
            self.calls += 1
            raise EndpointConnectionError(endpoint_url=self.meta.endpoint_url)

        return call


class UpClient:
    def __init__(self, endpoint: str) -> None:
        self.meta = _Meta(endpoint)
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        if name not in OPERATIONS:
            raise AttributeError(name)

        def call(**_kwargs: Any) -> Any:
            self.calls += 1
            return {"Events": []}

        return call


class DeniedClient:
    def __init__(self) -> None:
        self.meta = _Meta("https://denied.example")

    def __getattr__(self, name: str) -> Any:
        if name not in OPERATIONS:
            raise AttributeError(name)

        def call(**_kwargs: Any) -> Any:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, name)

        return call


def _recorder(allowed: str = "LookupEvents") -> CallRecorder:
    return CallRecorder(CollectionScope(now=NOW), lambda _s: None, frozenset({allowed}))


def test_the_second_call_to_an_unreachable_endpoint_makes_no_network_call() -> None:
    recorder = _recorder()
    down = DownClient()
    with pytest.raises(ConnectionFailedError) as first:
        recorder.call(down, "LookupEvents")
    assert not first.value.repeat
    with pytest.raises(ConnectionFailedError) as second:
        recorder.call(down, "LookupEvents")
    assert second.value.repeat
    assert down.calls == 1


def test_a_different_endpoint_is_not_blocked_by_an_unreachable_one() -> None:
    recorder = _recorder()
    with pytest.raises(ConnectionFailedError):
        recorder.call(DownClient("https://ec2.example"), "LookupEvents")
    up = UpClient("https://elasticloadbalancing.example")
    assert recorder.call(up, "LookupEvents") == {"Events": []}
    assert up.calls == 1


def test_cloudtrail_with_many_seeds_makes_one_call_and_one_note_when_unreachable() -> None:
    down = DownClient()
    scope = CollectionScope(
        now=NOW, seed_resource_ids=tuple(f"seed-{n}" for n in range(30)),
        creation_event_names=("CreateBucket",),
    )
    snapshot = CloudTrailCollector().collect(lambda *_a: down, "111111111111", "ap-south-1", scope)
    assert down.calls == 1
    assert snapshot.status == "error"
    assert len(snapshot.notes) == 1
    assert "EndpointConnectionError" in snapshot.notes[0]


def test_every_client_is_built_with_short_timeouts_and_a_single_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing-not-a-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing-not-a-secret")
    client = ambient_client("sts", "111111111111", "ap-south-1")
    assert isinstance(CLIENT_CONFIG, Config)
    assert client.meta.config.connect_timeout == 5
    assert client.meta.config.read_timeout == 30
    assert client.meta.config.retries["total_max_attempts"] == 2  # one retry


def test_collect_snapshots_reports_each_source_as_it_starts_and_finishes() -> None:
    seen: list[tuple[str, str]] = []
    snapshots = collect_snapshots(
        lambda *_a: DeniedClient(), "111111111111", "ap-south-1", Ruleset(), NOW,
        lambda kind, detail: seen.append((kind, detail)),
    )
    assert [s.source for s in snapshots] == ["tags", "stacks", "topology", "cloudtrail"]
    assert seen == [
        ("source_started", "tags"), ("source_done", "tags:denied"),
        ("source_started", "stacks"), ("source_done", "stacks:denied"),
        ("source_started", "topology"), ("source_done", "topology:denied"),
        ("source_started", "cloudtrail"), ("source_done", "cloudtrail:empty"),
    ]
