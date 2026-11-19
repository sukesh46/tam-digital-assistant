"""Write the golden-scenario raw snapshots (S1 to S11) from compact declarations.

Fake data only. Run from the project root:  python tests/fixtures/build_scenarios.py
Output: tests/fixtures/raw/<scenario>/<account>/<source>.json and
        tests/fixtures/raw/<scenario>/workspace.json
Every page is shaped like a real boto3 response; tests/unit/test_fixture_shapes.py checks
them against the botocore service models.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from wg_test_helpers import ct_event  # noqa: E402

RAW = HERE / "raw"
A, B, C = "111111111111", "222222222222", "333333333333"
REGION = "eu-west-1"
COLLECTED_AT = "2026-01-15T12:00:00Z"
DEPLOYER = "arn:aws:sts::111111111111:assumed-role/deployer/ci"


def stack_arn(account: str, name: str, n: int) -> str:
    return (
        f"arn:aws:cloudformation:{REGION}:{account}:stack/{name}/"
        f"00000000-0000-0000-0000-{n:012d}"
    )


def lb_arn(account: str, name: str) -> str:
    return (
        f"arn:aws:elasticloadbalancing:{REGION}:{account}:loadbalancer/app/"
        f"{name}/0000000000000001"
    )


def tg_arn(account: str, name: str) -> str:
    return f"arn:aws:elasticloadbalancing:{REGION}:{account}:targetgroup/{name}/0000000000000001"


def ec2_arn(account: str, kind: str, rid: str) -> str:
    return f"arn:aws:ec2:{REGION}:{account}:{kind}/{rid}"


@dataclass
class Stack:
    name: str
    n: int
    resources: list[tuple[str, str, str]]  # (logical id, type, physical id)
    parent: str | None = None
    status: str = "CREATE_COMPLETE"


@dataclass
class Topology:
    vpcs: list[str] = field(default_factory=list)
    subnets: list[tuple[str, str]] = field(default_factory=list)  # (subnet, vpc)
    sgs: list[tuple[str, str]] = field(default_factory=list)  # (group, vpc)
    instances: list[tuple[str, str, str, list[str]]] = field(default_factory=list)
    lbs: list[tuple[str, str, list[str], list[str]]] = field(default_factory=list)
    tgs: list[tuple[str, str, list[str], list[str]]] = field(default_factory=list)
    peerings: list[tuple[str, tuple[str, str], tuple[str, str]]] = field(default_factory=list)


class Writer:
    """Writes the snapshot files for one scenario."""

    def __init__(self, scenario: str, approved: list[str]) -> None:
        self.scenario = scenario
        self.root = RAW / scenario
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        workspace = {"workspace_id": f"ws-{scenario}", "customer": "example-customer",
                     "approved_account_ids": approved, "regions": [REGION]}
        (self.root / "workspace.json").write_text(json.dumps(workspace, indent=2) + "\n")

    def write(self, account: str, source: str, payload: dict[str, list[dict[str, Any]]],
              status: str = "ok", notes: list[str] | None = None,
              operations: list[str] | None = None) -> None:
        ops = operations if operations is not None else [
            op for op, pages in payload.items() if not op.startswith("_") for _ in pages]
        snapshot = {
            "snapshot_id": f"{self.scenario}-{account}-{REGION}-{source}",
            "source": source, "account_id": account, "region": REGION,
            "collected_at": COLLECTED_AT, "operations_called": ops, "payload": payload,
            "status": status, "notes": notes or [],
        }
        path = self.root / account / f"{source}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")

    def tags(self, account: str, mappings: list[tuple[str, dict[str, str]]]) -> None:
        page = {"ResourceTagMappingList": [
            {"ResourceARN": arn, "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]}
            for arn, tags in mappings], "PaginationToken": ""}
        self.write(account, "tags", {"GetResources": [page]})

    def stacks(self, account: str, stacks: list[Stack]) -> None:
        summaries = []
        for s in stacks:
            summary: dict[str, Any] = {
                "StackId": stack_arn(account, s.name, s.n), "StackName": s.name,
                "CreationTime": "2025-12-01T10:00:00Z", "StackStatus": s.status}
            if s.parent:
                parent = next(p for p in stacks if p.name == s.parent)
                summary["ParentId"] = summary["RootId"] = stack_arn(account, parent.name,
                                                                    parent.n)
            summaries.append(summary)
        payload: dict[str, list[dict[str, Any]]] = {"ListStacks": [{"StackSummaries": summaries}]}
        pages = []
        for s in stacks:
            pages.append({"StackResources": [{
                "StackName": s.name, "StackId": stack_arn(account, s.name, s.n),
                "LogicalResourceId": logical, "PhysicalResourceId": physical,
                "ResourceType": rtype, "Timestamp": "2025-12-01T10:05:00Z",
                "ResourceStatus": "CREATE_COMPLETE"} for logical, rtype, physical in s.resources]})
        if pages:
            payload["DescribeStackResources"] = pages
        self.write(account, "stacks", payload)

    def topology(self, account: str, t: Topology) -> None:
        reservations = [{"ReservationId": f"r-{i:04d}", "OwnerId": account, "Instances": [{
            "InstanceId": iid, "SubnetId": subnet, "VpcId": vpc,
            "SecurityGroups": [{"GroupId": g, "GroupName": g} for g in groups],
            "State": {"Code": 16, "Name": "running"}}]}
            for i, (iid, subnet, vpc, groups) in enumerate(t.instances)]
        payload: dict[str, list[dict[str, Any]]] = {
            "DescribeVpcs": [{"Vpcs": [{"VpcId": v, "OwnerId": account, "IsDefault": False}
                                       for v in t.vpcs]}],
            "DescribeSubnets": [{"Subnets": [{"SubnetId": s, "VpcId": v, "OwnerId": account}
                                             for s, v in t.subnets]}],
            "DescribeSecurityGroups": [{"SecurityGroups": [
                {"GroupId": g, "GroupName": g, "VpcId": v, "OwnerId": account}
                for g, v in t.sgs]}],
            "DescribeInstances": [{"Reservations": reservations}],
            "DescribeVpcPeeringConnections": [{"VpcPeeringConnections": [{
                "VpcPeeringConnectionId": pid,
                "RequesterVpcInfo": {"OwnerId": req[0], "VpcId": req[1], "Region": REGION},
                "AccepterVpcInfo": {"OwnerId": acc[0], "VpcId": acc[1], "Region": REGION},
                "Status": {"Code": "active", "Message": "Active"}}
                for pid, req, acc in t.peerings]}],
            "DescribeTransitGatewayAttachments": [{"TransitGatewayAttachments": []}],
            "DescribeLoadBalancers": [{"LoadBalancers": [{
                "LoadBalancerArn": lb_arn(account, name), "LoadBalancerName": name,
                "VpcId": vpc, "Type": "application",
                "AvailabilityZones": [{"ZoneName": f"{REGION}a", "SubnetId": s} for s in subnets],
                "SecurityGroups": groups} for name, vpc, subnets, groups in t.lbs]}],
            "DescribeTargetGroups": [{"TargetGroups": [{
                "TargetGroupArn": tg_arn(account, name), "TargetGroupName": name,
                "VpcId": vpc, "TargetType": "instance",
                "LoadBalancerArns": [lb_arn(account, lb) for lb in lbs]}
                for name, vpc, lbs, _ in t.tgs]}],
        }
        health = [{"_TargetGroupArn": tg_arn(account, name), "TargetHealthDescriptions": [
            {"Target": {"Id": target, "Port": 80}, "TargetHealth": {"State": "healthy"}}
            for target in targets]} for name, _, _, targets in t.tgs]
        if health:
            payload["DescribeTargetHealth"] = health
        self.write(account, "topology", payload)

    def cloudtrail(self, account: str, seeds: list[str], events: list[dict[str, Any]]) -> None:
        self.write(account, "cloudtrail", {"_seeds": [{"ResourceIds": sorted(seeds)}],
                                           "LookupEvents": [{"Events": events}]},
                   operations=["LookupEvents"] * max(1, len(seeds)))

    def empty(self, account: str, source: str) -> None:
        payload: dict[str, dict[str, list[dict[str, Any]]]] = {
            "tags": {"GetResources": [{"ResourceTagMappingList": [], "PaginationToken": ""}]},
            "stacks": {"ListStacks": [{"StackSummaries": []}]},
            "cloudtrail": {"_seeds": [{"ResourceIds": []}]},
        }
        if source == "topology":
            self.topology(account, Topology())
            return
        self.write(account, source, payload[source], status="empty",
                   operations=[] if source == "cloudtrail" else None)


INST, RDS, SG_T, VPC_T, SUB_T = ("AWS::EC2::Instance", "AWS::RDS::DBInstance",
                                 "AWS::EC2::SecurityGroup", "AWS::EC2::VPC", "AWS::EC2::Subnet")
LB_T, TG_T = "AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::ElasticLoadBalancingV2::TargetGroup"


def s1() -> None:
    w = Writer("s1", [A])
    w.stacks(A, [
        Stack("orders", 1, [("Web1", INST, "i-0s1orders1"), ("Web2", INST, "i-0s1orders2"),
                            ("Db", RDS, "orders-db"), ("Alb", LB_T, lb_arn(A, "orders-alb")),
                            ("Tg", TG_T, tg_arn(A, "orders-tg")), ("Sg", SG_T, "sg-0s1orders")]),
        Stack("billing", 2, [("Web1", INST, "i-0s1billing1"), ("Db", RDS, "billing-db"),
                             ("Alb", LB_T, lb_arn(A, "billing-alb")),
                             ("Tg", TG_T, tg_arn(A, "billing-tg")),
                             ("Sg", SG_T, "sg-0s1billing")]),
    ])
    w.topology(A, Topology(
        vpcs=["vpc-0s1"], subnets=[("subnet-0s1a", "vpc-0s1"), ("subnet-0s1b", "vpc-0s1")],
        sgs=[("sg-0s1orders", "vpc-0s1"), ("sg-0s1billing", "vpc-0s1")],
        instances=[("i-0s1orders1", "subnet-0s1a", "vpc-0s1", ["sg-0s1orders"]),
                   ("i-0s1orders2", "subnet-0s1a", "vpc-0s1", ["sg-0s1orders"]),
                   ("i-0s1billing1", "subnet-0s1b", "vpc-0s1", ["sg-0s1billing"])],
        lbs=[("orders-alb", "vpc-0s1", ["subnet-0s1a"], ["sg-0s1orders"]),
             ("billing-alb", "vpc-0s1", ["subnet-0s1b"], ["sg-0s1billing"])],
        tgs=[("orders-tg", "vpc-0s1", ["orders-alb"], ["i-0s1orders1", "i-0s1orders2"]),
             ("billing-tg", "vpc-0s1", ["billing-alb"], ["i-0s1billing1"])],
    ))
    w.tags(A, [(ec2_arn(A, "instance", "i-0s1orders1"), {"Name": "orders-web-1"}),
               (ec2_arn(A, "instance", "i-0s1billing1"), {"Name": "billing-web-1"})])
    w.cloudtrail(A, ["i-0s1orders1", "i-0s1orders2", "i-0s1billing1"], [])


def s2() -> None:
    w = Writer("s2", [A])
    w.tags(A, [
        (ec2_arn(A, "instance", "i-0s2orders1"), {"app": "orders", "Name": "orders-1"}),
        (ec2_arn(A, "instance", "i-0s2orders2"), {"app": "orders"}),
        (f"arn:aws:rds:{REGION}:{A}:db:orders-db", {"app": "orders"}),
        ("arn:aws:s3:::orders-assets-example", {"app": "orders", "env": "prod"}),
        (ec2_arn(A, "instance", "i-0s2billing1"), {"app": "billing"}),
        (f"arn:aws:lambda:{REGION}:{A}:function:billing-fn", {"app": "billing"}),
    ])
    w.topology(A, Topology(
        vpcs=["vpc-0s2"], subnets=[("subnet-0s2a", "vpc-0s2")], sgs=[("sg-0s2", "vpc-0s2")],
        instances=[(i, "subnet-0s2a", "vpc-0s2", ["sg-0s2"])
                   for i in ("i-0s2orders1", "i-0s2orders2", "i-0s2billing1")]))
    w.empty(A, "stacks")
    w.empty(A, "cloudtrail")


def s3() -> None:
    w = Writer("s3", [A])
    w.tags(A, [
        (ec2_arn(A, "instance", "i-0s3orders1"), {"app": "orders"}),
        (ec2_arn(A, "instance", "i-0s3orders2"), {"app": "orders"}),
        (ec2_arn(A, "instance", "i-0s3billing1"), {"app": "billing"}),
        (ec2_arn(A, "instance", "i-0s3billing2"), {"app": "billing"}),
        (ec2_arn(A, "instance", "i-0s3both"), {"app": "orders", "App": "billing"}),
    ])
    w.empty(A, "stacks")
    w.empty(A, "topology")
    w.empty(A, "cloudtrail")


def s4() -> None:
    w = Writer("s4", [A])
    w.stacks(A, [Stack("orders", 1, [("Web", INST, "i-0s4orders1"),
                                     ("Tg", TG_T, tg_arn(A, "orders-tg"))]),
                 Stack("billing", 2, [("Web", INST, "i-0s4billing1"),
                                      ("Tg", TG_T, tg_arn(A, "billing-tg"))])])
    w.topology(A, Topology(
        vpcs=["vpc-0s4"], subnets=[("subnet-0s4a", "vpc-0s4")],
        instances=[("i-0s4orders1", "subnet-0s4a", "vpc-0s4", []),
                   ("i-0s4billing1", "subnet-0s4a", "vpc-0s4", [])],
        lbs=[("shared-alb", "vpc-0s4", ["subnet-0s4a"], [])],
        tgs=[("orders-tg", "vpc-0s4", ["shared-alb"], ["i-0s4orders1"]),
             ("billing-tg", "vpc-0s4", ["shared-alb"], ["i-0s4billing1"])]))
    w.empty(A, "tags")
    w.empty(A, "cloudtrail")


def s5() -> None:
    w = Writer("s5", [A])
    w.topology(A, Topology(
        vpcs=["vpc-0s5a", "vpc-0s5b"],
        subnets=[("subnet-0s5a1", "vpc-0s5a"), ("subnet-0s5a2", "vpc-0s5a"),
                 ("subnet-0s5b1", "vpc-0s5b")],
        sgs=[("sg-0s5a", "vpc-0s5a")],
        instances=[("i-0s5one", "subnet-0s5a1", "vpc-0s5a", ["sg-0s5a"]),
                   ("i-0s5two", "subnet-0s5a2", "vpc-0s5a", ["sg-0s5a"]),
                   ("i-0s5three", "subnet-0s5b1", "vpc-0s5b", [])]))
    w.empty(A, "tags")
    w.empty(A, "stacks")
    w.empty(A, "cloudtrail")


def s6() -> None:
    w = Writer("s6", [A])
    w.stacks(A, [Stack("orders", 1, [("Web", INST, "i-0s6orders1"), ("Db", RDS, "orders-db")])])
    w.topology(A, Topology(
        vpcs=["vpc-0s6"], subnets=[("subnet-0s6a", "vpc-0s6")], sgs=[("sg-0s6", "vpc-0s6")],
        instances=[("i-0s6orders1", "subnet-0s6a", "vpc-0s6", ["sg-0s6"]),
                   ("i-0s6new", "subnet-0s6a", "vpc-0s6", ["sg-0s6"])]))
    w.empty(A, "tags")
    w.cloudtrail(A, ["i-0s6orders1", "orders-db"], [
        ct_event("s6-e1", "RunInstances", "2026-01-10T10:00:00Z", [(INST, "i-0s6orders1")]),
        ct_event("s6-e2", "CreateDBInstance", "2026-01-10T10:04:00Z", [(RDS, "orders-db")]),
        ct_event("s6-e3", "RunInstances", "2026-01-10T10:15:00Z", [(INST, "i-0s6new")]),
    ])


def s7() -> None:
    w = Writer("s7", [A])
    w.stacks(A, [
        Stack("platform", 1, [("Network", "AWS::CloudFormation::Stack",
                               stack_arn(A, "platform-Network-XYZ", 2)),
                              ("Alerts", "AWS::SNS::Topic",
                               f"arn:aws:sns:{REGION}:{A}:platform-alerts")]),
        Stack("platform-Network-XYZ", 2, [("Vpc", VPC_T, "vpc-0s7"),
                                          ("Subnet", SUB_T, "subnet-0s7a")],
              parent="platform"),
    ])
    w.topology(A, Topology(vpcs=["vpc-0s7"], subnets=[("subnet-0s7a", "vpc-0s7")]))
    w.empty(A, "tags")
    w.empty(A, "cloudtrail")


def s8() -> None:
    w = Writer("s8", [A, B])
    w.stacks(A, [Stack("app", 1, [("Web", INST, "i-0s8app1"), ("Vpc", VPC_T, "vpc-0s8a")])])
    peerings = [("pcx-0s8ab", (A, "vpc-0s8a"), (B, "vpc-0s8b")),
                ("pcx-0s8ac", (A, "vpc-0s8a"), (C, "vpc-0s8c"))]
    w.topology(A, Topology(vpcs=["vpc-0s8a"],
                           instances=[("i-0s8app1", "subnet-0s8a", "vpc-0s8a", [])],
                           subnets=[("subnet-0s8a", "vpc-0s8a")], peerings=peerings))
    w.empty(A, "tags")
    w.empty(A, "cloudtrail")
    w.topology(B, Topology(vpcs=["vpc-0s8b"], peerings=peerings[:1]))
    w.empty(B, "tags")
    w.empty(B, "stacks")
    w.empty(B, "cloudtrail")


def s9() -> None:
    w = Writer("s9", [A])
    w.stacks(A, [Stack("orders", 1, [("Web", INST, "i-0s9orders1"), ("Db", RDS, "orders-db")])])
    w.topology(A, Topology(vpcs=["vpc-0s9"], subnets=[("subnet-0s9a", "vpc-0s9")],
                           instances=[("i-0s9orders1", "subnet-0s9a", "vpc-0s9", [])]))
    w.empty(A, "tags")
    w.write(A, "cloudtrail", {"_seeds": [{"ResourceIds": ["i-0s9orders1", "orders-db"]}]},
            status="denied", notes=["LookupEvents: access denied (AccessDeniedException)"],
            operations=["LookupEvents"])


def s10() -> None:
    w = Writer("s10", [A])
    for source in ("tags", "stacks", "topology", "cloudtrail"):
        w.empty(A, source)


def s11() -> None:
    w = Writer("s11", [A])
    w.stacks(A, [Stack("orders", 1, [("Web", INST, "i-0s11orders1")]),
                 Stack("billing", 2, [("Web", INST, "i-0s11billing1")])])
    w.topology(A, Topology(
        vpcs=["vpc-0s11"], subnets=[("subnet-0s11", "vpc-0s11")], sgs=[("sg-0s11", "vpc-0s11")],
        instances=[(i, "subnet-0s11", "vpc-0s11", ["sg-0s11"])
                   for i in ("i-0s11orders1", "i-0s11billing1", "i-0s11mystery")]))
    w.empty(A, "tags")
    w.empty(A, "cloudtrail")


SCENARIOS = [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11]

if __name__ == "__main__":
    for build in SCENARIOS:
        build()
        print(f"wrote {build.__name__}")
