"""Network topology collector (EC2 and ELBv2 describe calls).

Status: real
Purpose: collect VPCs, subnets, security groups, instances, peering connections, transit
    gateway attachments, load balancers, target groups and target health.
Inputs: client factory, scope.
Outputs: ``RawSnapshot``. ``DescribeTargetHealth`` pages carry ``_TargetGroupArn``.
Planned dependencies: boto3.
Notes: a denied operation is noted and the remaining operations still run.
"""

from __future__ import annotations

from typing import ClassVar

from workload_graph.collectors.base import (
    BaseCollector,
    CallRecorder,
    ClientFactory,
    CollectionScope,
)
from workload_graph.models import SourceName

EC2_OPERATIONS: tuple[str, ...] = (
    "DescribeVpcs",
    "DescribeSubnets",
    "DescribeSecurityGroups",
    "DescribeInstances",
    "DescribeVpcPeeringConnections",
    "DescribeTransitGatewayAttachments",
)


class TopologyCollector(BaseCollector):
    """Collects EC2 and ELBv2 describe output."""

    source: ClassVar[SourceName] = "topology"
    allowed_operations: ClassVar[frozenset[str]] = frozenset(
        set(EC2_OPERATIONS)
        | {"DescribeLoadBalancers", "DescribeTargetGroups", "DescribeTargetHealth"})

    def _run(self, recorder: CallRecorder, client_factory: ClientFactory, account_id: str,
             region: str, scope: CollectionScope) -> None:
        ec2 = client_factory("ec2", account_id, region)
        for operation in EC2_OPERATIONS:
            def describe(operation: str = operation) -> None:
                for page in recorder.paginate(ec2, operation, {}, "NextToken", "NextToken"):
                    recorder.record(operation, page)

            if not recorder.step(operation, describe):
                return

        elb = client_factory("elbv2", account_id, region)

        def load_balancers() -> None:
            for page in recorder.paginate(elb, "DescribeLoadBalancers", {}, "Marker",
                                          "NextMarker"):
                recorder.record("DescribeLoadBalancers", page)

        if not recorder.step("DescribeLoadBalancers", load_balancers):
            return
        target_groups: set[str] = set()

        def describe_target_groups() -> None:
            for page in recorder.paginate(elb, "DescribeTargetGroups", {}, "Marker",
                                          "NextMarker"):
                recorder.record("DescribeTargetGroups", page)
                for group in page.get("TargetGroups") or []:
                    if isinstance(group.get("TargetGroupArn"), str):
                        target_groups.add(group["TargetGroupArn"])

        if not recorder.step("DescribeTargetGroups", describe_target_groups):
            return
        for arn in sorted(target_groups):
            def health(arn: str = arn) -> None:
                page = recorder.call(elb, "DescribeTargetHealth", TargetGroupArn=arn)
                recorder.record("DescribeTargetHealth", page, TargetGroupArn=arn)

            if not recorder.step(f"DescribeTargetHealth {arn}", health):
                return
