"""Topology normaliser (EC2 and ELBv2 describe calls).

Status: real
Purpose: emit VPCs, subnets, security groups, instances, load balancers, target groups and
    transit gateways as resources, with container edges (resource -> container), load balancer
    target edges and cross-account references.
Inputs: ``RawSnapshot`` with ``Describe*`` pages. ``DescribeTargetHealth`` pages carry the
    collector metadata key ``_TargetGroupArn`` because the response does not name its group.
Outputs: ``NormalisedBatch``.
Planned dependencies: none.
Notes:
    - Edges go resource -> container (subnet, VPC, security group); no pairwise edges between
      co-located resources, which avoids quadratic growth.
    - ``LB_TARGET`` edges go from the target group AND from each of its load balancers to every
      resolvable target. IP targets are noted as unresolved, never guessed.
    - Peering and transit gateway attachments whose owners differ emit ``CROSS_ACCOUNT_REF``
      from the local end to a key in the other account. No access to that account follows.
"""

from __future__ import annotations

import re
from typing import Any

from workload_graph.ids import ResourceKey, arn_for, key_for_arn
from workload_graph.models import Edge, EdgeType, RawSnapshot, Resource
from workload_graph.normalisers.base import (
    BatchBuilder,
    NormalisedBatch,
    evidence,
    iter_items,
    tags_to_dict,
)
from workload_graph.ruleset import Ruleset

_ACCOUNT_RE = re.compile(r"[0-9]{12}")
_INACTIVE_PEERING = frozenset({"deleted", "deleting", "rejected", "failed", "expired"})
_INACTIVE_ATTACHMENT = frozenset({"deleted", "deleting", "failed", "rejected"})


class _Ctx:
    """Per-snapshot helpers."""

    def __init__(self, snapshot: RawSnapshot) -> None:
        self.snapshot = snapshot
        self.builder = BatchBuilder()

    def account_or_default(self, value: Any) -> str:
        """Use ``value`` as an account id if valid, else the snapshot account. Any: raw."""
        if isinstance(value, str) and _ACCOUNT_RE.fullmatch(value):
            return value
        return self.snapshot.account_id

    def key(self, resource_type: str, rid: str, account: str | None = None,
            region: str | None = None) -> ResourceKey:
        acct = account or self.snapshot.account_id
        reg = region or self.snapshot.region
        resolution = arn_for(resource_type, rid, acct, reg)
        if resolution.note:
            self.builder.note(f"topology: {resolution.note}")
        return key_for_arn(resolution.arn, acct, reg)

    def resource(self, key: ResourceKey, resource_type: str, item: dict[str, Any],
                 attributes: dict[str, str]) -> None:
        tags = tags_to_dict(item.get("Tags"))
        self.builder.add_resource(Resource(
            key=key, resource_type=resource_type, name=tags.get("Name"), tags=tags,
            attributes=attributes, sources=frozenset({"topology"}),
        ))

    def link(self, src: ResourceKey, dst: ResourceKey, edge_type: EdgeType, pointer: str,
             detail: str) -> None:
        self.builder.add_edge(Edge(src=src, dst=dst, edge_type=edge_type,
                                   evidence=(evidence(self.snapshot, pointer, detail),)))


def normalise_topology(snapshot: RawSnapshot, ruleset: Ruleset) -> NormalisedBatch:
    """Normalise a topology snapshot."""
    del ruleset  # no topology rules yet; kept for a uniform normaliser signature
    ctx = _Ctx(snapshot)
    _vpcs(ctx)
    _subnets(ctx)
    _security_groups(ctx)
    _instances(ctx)
    lbs_by_tg = _load_balancers_and_target_groups(ctx)
    _target_health(ctx, lbs_by_tg)
    _peering(ctx)
    _transit_gateway_attachments(ctx)
    return ctx.builder.build()


def _vpcs(ctx: _Ctx) -> None:
    for _pointer, vpc, _ in iter_items(ctx.snapshot, "DescribeVpcs", "Vpcs"):
        vid = vpc.get("VpcId")
        if not isinstance(vid, str):
            continue
        key = ctx.key("AWS::EC2::VPC", vid, ctx.account_or_default(vpc.get("OwnerId")))
        ctx.resource(key, "AWS::EC2::VPC", vpc, {
            "vpc_id": vid, "is_default": str(bool(vpc.get("IsDefault"))).lower()})


def _subnets(ctx: _Ctx) -> None:
    for pointer, subnet, _ in iter_items(ctx.snapshot, "DescribeSubnets", "Subnets"):
        sid, vid = subnet.get("SubnetId"), subnet.get("VpcId")
        if not isinstance(sid, str):
            continue
        owner = ctx.account_or_default(subnet.get("OwnerId"))
        arn = subnet.get("SubnetArn")
        key = (key_for_arn(arn, owner, ctx.snapshot.region) if isinstance(arn, str)
               else ctx.key("AWS::EC2::Subnet", sid, owner))
        attributes = {"subnet_id": sid}
        if isinstance(vid, str):
            attributes["vpc_id"] = vid
            ctx.link(key, ctx.key("AWS::EC2::VPC", vid, owner), EdgeType.SAME_VPC, pointer,
                     f"subnet {sid} in vpc {vid}")
        ctx.resource(key, "AWS::EC2::Subnet", subnet, attributes)


def _security_groups(ctx: _Ctx) -> None:
    for pointer, sg, _ in iter_items(ctx.snapshot, "DescribeSecurityGroups", "SecurityGroups"):
        gid, vid = sg.get("GroupId"), sg.get("VpcId")
        if not isinstance(gid, str):
            continue
        owner = ctx.account_or_default(sg.get("OwnerId"))
        key = ctx.key("AWS::EC2::SecurityGroup", gid, owner)
        attributes = {"group_name": str(sg.get("GroupName", ""))}
        if isinstance(vid, str):
            attributes["vpc_id"] = vid
            ctx.link(key, ctx.key("AWS::EC2::VPC", vid, owner), EdgeType.SAME_VPC, pointer,
                     f"security group {gid} in vpc {vid}")
        ctx.resource(key, "AWS::EC2::SecurityGroup", sg, attributes)


def _instances(ctx: _Ctx) -> None:
    for r_pointer, reservation, _ in iter_items(ctx.snapshot, "DescribeInstances",
                                                "Reservations"):
        for index, inst in enumerate(reservation.get("Instances") or []):
            pointer = f"{r_pointer}/Instances/{index}"
            iid = inst.get("InstanceId")
            if not isinstance(iid, str):
                continue
            state = (inst.get("State") or {}).get("Name")
            if state == "terminated":
                ctx.builder.note(f"topology: instance {iid} is terminated; skipped")
                continue
            key = ctx.key("AWS::EC2::Instance", iid)
            attributes: dict[str, str] = {}
            subnet, vpc = inst.get("SubnetId"), inst.get("VpcId")
            if isinstance(subnet, str):
                attributes["subnet_id"] = subnet
                ctx.link(key, ctx.key("AWS::EC2::Subnet", subnet), EdgeType.SAME_SUBNET,
                         pointer, f"instance {iid} in subnet {subnet}")
            if isinstance(vpc, str):
                attributes["vpc_id"] = vpc
                ctx.link(key, ctx.key("AWS::EC2::VPC", vpc), EdgeType.SAME_VPC, pointer,
                         f"instance {iid} in vpc {vpc}")
            for group in inst.get("SecurityGroups") or []:
                gid = group.get("GroupId") if isinstance(group, dict) else None
                if isinstance(gid, str):
                    ctx.link(key, ctx.key("AWS::EC2::SecurityGroup", gid),
                             EdgeType.SHARED_SECURITY_GROUP, pointer,
                             f"instance {iid} uses security group {gid}")
            ctx.resource(key, "AWS::EC2::Instance", inst, attributes)


def _load_balancers_and_target_groups(ctx: _Ctx) -> dict[str, list[str]]:
    for pointer, lb, _ in iter_items(ctx.snapshot, "DescribeLoadBalancers", "LoadBalancers"):
        arn = lb.get("LoadBalancerArn")
        if not isinstance(arn, str):
            continue
        key = key_for_arn(arn, ctx.snapshot.account_id, ctx.snapshot.region)
        name = str(lb.get("LoadBalancerName", ""))
        attributes: dict[str, str] = {}
        vpc = lb.get("VpcId")
        if isinstance(vpc, str):
            attributes["vpc_id"] = vpc
            ctx.link(key, ctx.key("AWS::EC2::VPC", vpc), EdgeType.SAME_VPC, pointer,
                     f"load balancer {name} in vpc {vpc}")
        for zone in lb.get("AvailabilityZones") or []:
            subnet = zone.get("SubnetId") if isinstance(zone, dict) else None
            if isinstance(subnet, str):
                ctx.link(key, ctx.key("AWS::EC2::Subnet", subnet), EdgeType.SAME_SUBNET,
                         pointer, f"load balancer {name} in subnet {subnet}")
        for gid in lb.get("SecurityGroups") or []:
            if isinstance(gid, str):
                ctx.link(key, ctx.key("AWS::EC2::SecurityGroup", gid),
                         EdgeType.SHARED_SECURITY_GROUP, pointer,
                         f"load balancer {name} uses security group {gid}")
        ctx.builder.add_resource(Resource(
            key=key, resource_type="AWS::ElasticLoadBalancingV2::LoadBalancer", name=name,
            attributes=attributes, sources=frozenset({"topology"})))

    lbs_by_tg: dict[str, list[str]] = {}
    for pointer, tg, _ in iter_items(ctx.snapshot, "DescribeTargetGroups", "TargetGroups"):
        arn = tg.get("TargetGroupArn")
        if not isinstance(arn, str):
            continue
        key = key_for_arn(arn, ctx.snapshot.account_id, ctx.snapshot.region)
        name = str(tg.get("TargetGroupName", ""))
        lbs_by_tg[arn] = sorted(a for a in tg.get("LoadBalancerArns") or [] if isinstance(a, str))
        attributes = {}
        vpc = tg.get("VpcId")
        if isinstance(vpc, str):
            attributes["vpc_id"] = vpc
            ctx.link(key, ctx.key("AWS::EC2::VPC", vpc), EdgeType.SAME_VPC, pointer,
                     f"target group {name} in vpc {vpc}")
        ctx.builder.add_resource(Resource(
            key=key, resource_type="AWS::ElasticLoadBalancingV2::TargetGroup", name=name,
            attributes=attributes, sources=frozenset({"topology"})))
    return lbs_by_tg


def _target_health(ctx: _Ctx, lbs_by_tg: dict[str, list[str]]) -> None:
    acct, region = ctx.snapshot.account_id, ctx.snapshot.region
    for pointer, desc, page in iter_items(ctx.snapshot, "DescribeTargetHealth",
                                          "TargetHealthDescriptions"):
        tg_arn = page.get("_TargetGroupArn")
        target_id = (desc.get("Target") or {}).get("Id")
        if not isinstance(tg_arn, str) or not isinstance(target_id, str):
            ctx.builder.note(f"topology: target health entry without group or id at {pointer}")
            continue
        if target_id.startswith("i-"):
            target = ctx.key("AWS::EC2::Instance", target_id)
        elif target_id.startswith("arn:"):
            target = key_for_arn(target_id, acct, region)
        else:
            ctx.builder.note(
                f"topology: target {target_id} of {tg_arn} is an IP address; left unresolved")
            continue
        tg_key = key_for_arn(tg_arn, acct, region)
        ctx.link(tg_key, target, EdgeType.LB_TARGET, pointer,
                 f"target group targets {target_id}")
        for lb_arn in lbs_by_tg.get(tg_arn, []):
            ctx.link(key_for_arn(lb_arn, acct, region), target, EdgeType.LB_TARGET, pointer,
                     f"load balancer targets {target_id} via target group")


def _peering(ctx: _Ctx) -> None:
    local = ctx.snapshot.account_id
    for pointer, pcx, _ in iter_items(ctx.snapshot, "DescribeVpcPeeringConnections",
                                      "VpcPeeringConnections"):
        status = str((pcx.get("Status") or {}).get("Code", ""))
        if status in _INACTIVE_PEERING:
            continue
        sides = [pcx.get("RequesterVpcInfo") or {}, pcx.get("AccepterVpcInfo") or {}]
        owners = [ctx.account_or_default(s.get("OwnerId")) for s in sides]
        if owners[0] == owners[1]:
            continue
        if local not in owners:
            ctx.builder.note(f"topology: peering {pcx.get('VpcPeeringConnectionId')} has no "
                             "local side; skipped")
            continue
        mine = owners.index(local)
        here, there = sides[mine], sides[1 - mine]
        if not isinstance(here.get("VpcId"), str) or not isinstance(there.get("VpcId"), str):
            continue
        src = ctx.key("AWS::EC2::VPC", here["VpcId"], local, here.get("Region"))
        dst = ctx.key("AWS::EC2::VPC", there["VpcId"], owners[1 - mine], there.get("Region"))
        ctx.link(src, dst, EdgeType.CROSS_ACCOUNT_REF, pointer,
                 f"vpc peering {pcx.get('VpcPeeringConnectionId')} to account "
                 f"{owners[1 - mine]}")


def _transit_gateway_attachments(ctx: _Ctx) -> None:
    local = ctx.snapshot.account_id
    for pointer, att, _ in iter_items(ctx.snapshot, "DescribeTransitGatewayAttachments",
                                      "TransitGatewayAttachments"):
        if str(att.get("State", "")) in _INACTIVE_ATTACHMENT:
            continue
        tgw_owner = ctx.account_or_default(att.get("TransitGatewayOwnerId"))
        res_owner = ctx.account_or_default(att.get("ResourceOwnerId"))
        tgw_id, res_id = att.get("TransitGatewayId"), att.get("ResourceId")
        if tgw_owner == res_owner or not isinstance(tgw_id, str) or not isinstance(res_id, str):
            continue
        res_type = "AWS::EC2::VPC" if att.get("ResourceType") == "vpc" else (
            f"AWS::EC2::TGWAttachment{str(att.get('ResourceType', 'Unknown')).title()}")
        tgw_key = ctx.key("AWS::EC2::TransitGateway", tgw_id, tgw_owner)
        res_key = ctx.key(res_type, res_id, res_owner)
        attachment = att.get("TransitGatewayAttachmentId")
        if local == res_owner:
            ctx.link(res_key, tgw_key, EdgeType.CROSS_ACCOUNT_REF, pointer,
                     f"attachment {attachment} to transit gateway in account {tgw_owner}")
        elif local == tgw_owner:
            ctx.builder.add_resource(Resource(
                key=tgw_key, resource_type="AWS::EC2::TransitGateway",
                attributes={"transit_gateway_id": tgw_id}, sources=frozenset({"topology"})))
            ctx.link(tgw_key, res_key, EdgeType.CROSS_ACCOUNT_REF, pointer,
                     f"attachment {attachment} from resource in account {res_owner}")
        else:
            ctx.builder.note(f"topology: attachment {attachment} has no local side; skipped")
