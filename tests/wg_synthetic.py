"""S12: a large synthetic scenario (about 2,000 resources), generated deterministically.

Fake data only. The layout mixes declared stacks, tag groups, shared load balancers and
security groups, undeclared instances (some created by a stack's principal) and isolated
resources, so every builder branch is exercised at scale.
"""

from __future__ import annotations

import random
from typing import Any

from wg_test_helpers import ACCT_A, REGION, ct_event, make_snapshot

from workload_graph.models import RawSnapshot, Workspace

N_VPCS, SUBNETS_PER_VPC, SGS_PER_VPC = 10, 4, 4
N_STACKS, INSTANCES_PER_STACK = 100, 8
N_UNDECLARED, N_TAGGED, N_TAG_GROUPS, N_LBS = 600, 100, 20, 10


def _stack_arn(k: int) -> str:
    return (f"arn:aws:cloudformation:{REGION}:{ACCT_A}:stack/svc-{k:03d}/"
            f"00000000-0000-0000-0000-{k:012d}")


def _tg_arn(k: int) -> str:
    return f"arn:aws:elasticloadbalancing:{REGION}:{ACCT_A}:targetgroup/tg-{k:03d}/{k:016d}"


def _lb_arn(j: int) -> str:
    return f"arn:aws:elasticloadbalancing:{REGION}:{ACCT_A}:loadbalancer/app/lb-{j:02d}/{j:016d}"


def _placement(k: int) -> tuple[str, str, str]:
    vpc = k % N_VPCS
    return (f"vpc-{vpc:02d}", f"subnet-{vpc:02d}{k % SUBNETS_PER_VPC}",
            f"sg-{vpc:02d}{(k // N_VPCS) % SGS_PER_VPC}")


def synthetic_snapshots(seed: int = 12) -> list[RawSnapshot]:
    """Generate the four S12 snapshots."""
    rng = random.Random(seed)
    vpcs = [{"VpcId": f"vpc-{v:02d}", "OwnerId": ACCT_A} for v in range(N_VPCS)]
    subnets = [{"SubnetId": f"subnet-{v:02d}{s}", "VpcId": f"vpc-{v:02d}", "OwnerId": ACCT_A}
               for v in range(N_VPCS) for s in range(SUBNETS_PER_VPC)]
    sgs = [{"GroupId": f"sg-{v:02d}{g}", "GroupName": f"sg-{v:02d}{g}", "VpcId": f"vpc-{v:02d}",
            "OwnerId": ACCT_A} for v in range(N_VPCS) for g in range(SGS_PER_VPC)]

    summaries: list[dict[str, Any]] = []
    stack_pages: list[dict[str, Any]] = []
    instances: list[dict[str, Any]] = []
    tgs: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    seeds: list[str] = []
    for k in range(N_STACKS):
        vpc, subnet, sg = _placement(k)
        summaries.append({"StackId": _stack_arn(k), "StackName": f"svc-{k:03d}",
                          "CreationTime": "2025-12-01T10:00:00Z",
                          "StackStatus": "CREATE_COMPLETE"})
        rows = [("Db", "AWS::RDS::DBInstance", f"db-{k:03d}"),
                ("Tg", "AWS::ElasticLoadBalancingV2::TargetGroup", _tg_arn(k))]
        ids = [f"i-s{k:03d}{n}" for n in range(INSTANCES_PER_STACK)]
        rows += [(f"Web{n}", "AWS::EC2::Instance", iid) for n, iid in enumerate(ids)]
        stack_pages.append({"StackResources": [{
            "StackName": f"svc-{k:03d}", "StackId": _stack_arn(k), "LogicalResourceId": lg,
            "PhysicalResourceId": pid, "ResourceType": rt, "Timestamp": "2025-12-01T10:05:00Z",
            "ResourceStatus": "CREATE_COMPLETE"} for lg, rt, pid in rows]})
        instances += [{"InstanceId": iid, "SubnetId": subnet, "VpcId": vpc,
                       "SecurityGroups": [{"GroupId": sg, "GroupName": sg}],
                       "State": {"Code": 16, "Name": "running"}} for iid in ids]
        tgs.append({"TargetGroupArn": _tg_arn(k), "TargetGroupName": f"tg-{k:03d}",
                    "VpcId": vpc, "TargetType": "instance",
                    "LoadBalancerArns": [_lb_arn(k // 2)] if k < 2 * N_LBS else []})
        health.append({"_TargetGroupArn": _tg_arn(k), "TargetHealthDescriptions": [
            {"Target": {"Id": iid, "Port": 80}} for iid in ids[:2]]})
        seeds.append(ids[0])
        minute = k % 50
        events.append(ct_event(f"s12-seed-{k}", "RunInstances",
                               f"2026-01-{1 + k // 50:02d}T10:{minute:02d}:00Z",
                               [("AWS::EC2::Instance", ids[0])],
                               principal=f"arn:aws:sts::{ACCT_A}:assumed-role/deploy-{k}/ci"))

    for u in range(N_UNDECLARED):
        k = rng.randrange(N_STACKS)
        vpc, subnet, sg = _placement(k if u % 3 else rng.randrange(N_STACKS))
        iid = f"i-u{u:04d}"
        instances.append({"InstanceId": iid, "SubnetId": subnet, "VpcId": vpc,
                          "SecurityGroups": [{"GroupId": sg, "GroupName": sg}] if u % 2 else [],
                          "State": {"Code": 16, "Name": "running"}})
        if u % 4 == 0:
            minute = (k % 50) + rng.randrange(0, 10)
            events.append(ct_event(f"s12-u-{u}", "RunInstances",
                                   f"2026-01-{1 + k // 50:02d}T10:{minute % 60:02d}:00Z",
                                   [("AWS::EC2::Instance", iid)],
                                   principal=f"arn:aws:sts::{ACCT_A}:assumed-role/deploy-{k}/ci"))

    mappings = []
    for t in range(N_TAGGED):
        iid = f"i-t{t:04d}"
        vpc, subnet, _ = _placement(t)
        instances.append({"InstanceId": iid, "SubnetId": subnet, "VpcId": vpc,
                          "State": {"Code": 16, "Name": "running"}})
        mappings.append({"ResourceARN": f"arn:aws:ec2:{REGION}:{ACCT_A}:instance/{iid}",
                         "Tags": [{"Key": "app", "Value": f"tagged-{t % N_TAG_GROUPS:02d}"}]})
    for b in range(40):
        mappings.append({"ResourceARN": f"arn:aws:s3:::s12-bucket-{b:03d}", "Tags": []})

    lbs = [{"LoadBalancerArn": _lb_arn(j), "LoadBalancerName": f"lb-{j:02d}",
            "VpcId": _placement(2 * j)[0], "Type": "application",
            "AvailabilityZones": [{"ZoneName": f"{REGION}a",
                                   "SubnetId": _placement(2 * j)[1]}]} for j in range(N_LBS)]

    topology = make_snapshot("topology", {
        "DescribeVpcs": [{"Vpcs": vpcs}], "DescribeSubnets": [{"Subnets": subnets}],
        "DescribeSecurityGroups": [{"SecurityGroups": sgs}],
        "DescribeInstances": [{"Reservations": [
            {"ReservationId": f"r-{i:05d}", "OwnerId": ACCT_A, "Instances": [inst]}
            for i, inst in enumerate(instances)]}],
        "DescribeVpcPeeringConnections": [{"VpcPeeringConnections": []}],
        "DescribeTransitGatewayAttachments": [{"TransitGatewayAttachments": []}],
        "DescribeLoadBalancers": [{"LoadBalancers": lbs}],
        "DescribeTargetGroups": [{"TargetGroups": tgs}],
        "DescribeTargetHealth": health,
    }, snapshot_id="s12-111111111111-topology")
    stacks = make_snapshot("stacks", {"ListStacks": [{"StackSummaries": summaries}],
                                      "DescribeStackResources": stack_pages},
                           snapshot_id="s12-111111111111-stacks")
    tags = make_snapshot("tags", {"GetResources": [{"ResourceTagMappingList": mappings}]},
                         snapshot_id="s12-111111111111-tags")
    cloudtrail = make_snapshot("cloudtrail", {"_seeds": [{"ResourceIds": sorted(seeds)}],
                                              "LookupEvents": [{"Events": events}]},
                               snapshot_id="s12-111111111111-cloudtrail")
    return [cloudtrail, stacks, tags, topology]


def synthetic_workspace() -> Workspace:
    """Approved scope for S12."""
    return Workspace(workspace_id="ws-s12", customer="example-customer",
                     approved_account_ids=[ACCT_A], regions=[REGION])
