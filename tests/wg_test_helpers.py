"""Helpers shared by tests: snapshot construction and botocore shape validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import botocore.session
from botocore.validate import ParamValidator

from workload_graph.models import RawSnapshot, SourceName

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RAW_DIR = FIXTURES_DIR / "raw"
GOLDEN_DIR = FIXTURES_DIR / "golden"
FROZEN_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
ACCT_A = "111111111111"
ACCT_B = "222222222222"
ACCT_C = "333333333333"
REGION = "eu-west-1"

OPERATION_SERVICE: dict[str, str] = {
    "GetResources": "resourcegroupstaggingapi",
    "ListStacks": "cloudformation",
    "DescribeStackResources": "cloudformation",
    "DescribeVpcs": "ec2",
    "DescribeSubnets": "ec2",
    "DescribeSecurityGroups": "ec2",
    "DescribeInstances": "ec2",
    "DescribeVpcPeeringConnections": "ec2",
    "DescribeTransitGatewayAttachments": "ec2",
    "DescribeLoadBalancers": "elbv2",
    "DescribeTargetGroups": "elbv2",
    "DescribeTargetHealth": "elbv2",
    "LookupEvents": "cloudtrail",
}

_SESSION = botocore.session.get_session()


def make_snapshot(
    source: SourceName,
    payload: dict[str, list[dict[str, Any]]],
    *,
    account_id: str = ACCT_A,
    region: str = REGION,
    snapshot_id: str | None = None,
    status: str = "ok",
    collected_at: datetime = FROZEN_NOW,
) -> RawSnapshot:
    """Build a RawSnapshot for tests."""
    return RawSnapshot.model_validate(
        {
            "snapshot_id": snapshot_id or f"test-{account_id}-{region}-{source}",
            "source": source,
            "account_id": account_id,
            "region": region,
            "collected_at": collected_at,
            "operations_called": sorted(payload),
            "payload": payload,
            "status": status,
            "notes": [],
        }
    )


def load_snapshot(relative: str) -> RawSnapshot:
    """Load a RawSnapshot JSON file relative to tests/fixtures/raw."""
    return RawSnapshot.model_validate_json((RAW_DIR / relative).read_text())


def strip_metadata(page: dict[str, Any]) -> dict[str, Any]:
    """Drop collector metadata keys (prefixed with '_') from a page."""
    return {k: v for k, v in page.items() if not k.startswith("_")}


def shape_errors(operation: str, page: dict[str, Any]) -> str:
    """Validate a response page against the botocore output shape; '' means valid."""
    service = OPERATION_SERVICE[operation]
    shape = _SESSION.get_service_model(service).operation_model(operation).output_shape
    report = ParamValidator().validate(strip_metadata(page), shape)
    return str(report.generate_report()) if report.has_errors() else ""


SCENARIOS = [f"s{i}" for i in range(1, 12)]


def scenario_snapshots(name: str) -> list[RawSnapshot]:
    """All snapshots of a golden scenario, in file order."""
    root = RAW_DIR / name
    return [RawSnapshot.model_validate_json(p.read_text())
            for p in sorted(root.glob("*/*.json"))]


def scenario_workspace(name: str) -> Any:
    """The workspace (approved scope) of a golden scenario."""
    from workload_graph.models import Workspace

    return Workspace.model_validate_json((RAW_DIR / name / "workspace.json").read_text())


def build_from_snapshots(snapshots: list[RawSnapshot], workspace: Any,
                         ruleset: Any = None, rng: Any = None) -> Any:
    """Normalise, store and build; ``rng`` (a random.Random) shuffles every input order."""
    from workload_graph.graph.build import build_workloads
    from workload_graph.normalisers import normalise
    from workload_graph.probe import probe_sources
    from workload_graph.ruleset import Ruleset
    from workload_graph.store.memory import InMemoryGraphStore

    rs = ruleset or Ruleset()
    snaps = list(snapshots)
    if rng is not None:
        rng.shuffle(snaps)
    resources: list[Any] = []
    edges: list[Any] = []
    notes: list[str] = []
    for snap in snaps:
        batch = normalise(snap, rs)
        resources.extend(batch.resources)
        edges.extend(batch.edges)
        notes.extend(batch.notes)
    if rng is not None:
        rng.shuffle(resources)
        rng.shuffle(edges)
        rng.shuffle(notes)
    store = InMemoryGraphStore()
    items: list[Any] = [("r", r) for r in resources] + [("e", e) for e in edges]
    if rng is not None:
        rng.shuffle(items)
    for kind, item in items:
        if kind == "r":
            store.add_resource(item)
        else:
            store.add_edge(item)
    probe = probe_sources(snaps, workspace.approved_account_ids)
    return build_workloads(store, rs, workspace, probe, [s.snapshot_id for s in snaps],
                           notes=notes), store


def placed_keys(result: Any) -> list[Any]:
    """Every resource key placed anywhere in a BuildResult (with duplicates kept)."""
    keys = [m.key for w in result.workloads for m in w.members]
    keys += [s.key for s in result.shared_infrastructure]
    keys += [m.key for g in result.residual_groups for m in g.members]
    keys += [u.key for u in result.unmapped]
    return keys


def gk(name: str, account: str = ACCT_A, kind: str = "instance") -> Any:
    """A ResourceKey for graph tests: ``arn:aws:ec2:eu-west-1:{account}:{kind}/{name}``."""
    from workload_graph.ids import ResourceKey

    return ResourceKey(account_id=account, region=REGION,
                       arn=f"arn:aws:ec2:{REGION}:{account}:{kind}/{name}")


def gres(key: Any, *, sources: tuple[str, ...] = ("stacks", "topology"),
         resource_type: str = "AWS::EC2::Instance", **attributes: str) -> Any:
    """A Resource for graph tests; keyword arguments become attributes."""
    from workload_graph.models import Resource

    tags = {}
    if "tags" in attributes:
        tags = json.loads(attributes.pop("tags"))
    return Resource.model_validate({
        "key": key, "resource_type": resource_type, "attributes": attributes, "tags": tags,
        "sources": frozenset(sources)})


def gedge(src: Any, dst: Any, edge_type: Any, ref: str | None = None,
          detail: str = "test edge") -> Any:
    """An Edge with one evidence record for graph tests."""
    from workload_graph.models import Edge, Evidence

    source = {"STACK_MEMBER": "stacks", "TAG_WORKLOAD_KEY": "tags",
              "CREATED_BY_SAME_PRINCIPAL": "cloudtrail"}.get(str(edge_type), "topology")
    return Edge(src=src, dst=dst, edge_type=edge_type, evidence=(Evidence(
        source=source, observed_at=FROZEN_NOW,  # type: ignore[arg-type]
        raw_ref=ref or f"test#{src.arn}->{getattr(dst, 'arn', '')}:{edge_type}",
        detail=detail),))


def ct_event(
    event_id: str,
    event_name: str,
    event_time: str,
    resources: list[tuple[str, str]],
    *,
    principal: str = "arn:aws:sts::111111111111:assumed-role/deployer/ci",
    identity_account: str = ACCT_A,
    recipient_account: str = ACCT_A,
    raw: str | None = None,
) -> dict[str, Any]:
    """Build one LookupEvents event with an embedded CloudTrailEvent JSON string."""
    body = {
        "eventVersion": "1.08",
        "eventTime": event_time,
        "eventName": event_name,
        "awsRegion": REGION,
        "sourceIPAddress": "192.0.2.10",
        "userAgent": "example-agent/1.0",
        "userIdentity": {"type": "AssumedRole", "arn": principal, "accountId": identity_account},
        "recipientAccountId": recipient_account,
    }
    return {
        "EventId": event_id,
        "EventName": event_name,
        "ReadOnly": "false",
        "EventTime": event_time,
        "EventSource": "ec2.amazonaws.com",
        "Username": "ci",
        "Resources": [{"ResourceType": t, "ResourceName": n} for t, n in resources],
        "CloudTrailEvent": raw if raw is not None else json.dumps(body, sort_keys=True),
    }
