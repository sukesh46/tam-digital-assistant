"""Resource identity: account ids, canonical resource keys and ARN construction.

Status: real
Purpose: canonical identity for every resource, ``(account_id, region, arn)``.
Inputs: account ids, CloudFormation-style resource types, physical ids, ARNs.
Outputs: ``AccountId``, ``ResourceKey``, ``arn_for()``, ``key_for_arn()``,
    ``resource_type_from_arn()``.
Planned dependencies: pydantic.
Notes: ARNs alone are not unique for some resources (S3 buckets carry no account), so the
    key includes the account. Resource types use CloudFormation names (``AWS::EC2::Instance``)
    as the single vocabulary across sources.
"""

from __future__ import annotations

import re
from typing import Annotated, NamedTuple

from pydantic import AfterValidator, BaseModel, ConfigDict

# ASSUMPTION: partition "aws" only; China and GovCloud partitions are out of scope (D13).
PARTITION = "aws"
GLOBAL_REGION = "global"
UNRESOLVED_PREFIX = "unresolved:"

_ACCOUNT_RE = re.compile(r"[0-9]{12}")
_UNRESOLVED_RE = re.compile(r"^unresolved:([A-Za-z0-9]+::[A-Za-z0-9]+::[A-Za-z0-9]+):(.*)$")


def validate_account_id(value: str) -> str:
    """Return ``value`` if it is exactly 12 ASCII digits, otherwise raise ``ValueError``."""
    if not isinstance(value, str) or not _ACCOUNT_RE.fullmatch(value):
        raise ValueError(f"account id must be exactly 12 digits, got {value!r}")
    return value


AccountId = Annotated[str, AfterValidator(validate_account_id)]


class ResourceKey(BaseModel):
    """Canonical identity of a resource: ``(account_id, region, arn)``.

    ``region`` is ``"global"`` for resources whose ARN has no region (IAM, S3).
    Keys order lexicographically by ``key_str()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: AccountId
    region: str
    arn: str

    def key_str(self) -> str:
        """Return ``"{account_id}|{region}|{arn}"``."""
        return f"{self.account_id}|{self.region}|{self.arn}"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ResourceKey):
            return NotImplemented
        return self.key_str() < other.key_str()


class ArnResolution(NamedTuple):
    """Result of ``arn_for``: the ARN (or placeholder) and an optional note."""

    arn: str
    note: str | None


# Explicit, deliberately small table. Templates receive region, account and physical id.
_ARN_TEMPLATES: dict[str, str] = {
    "AWS::EC2::Instance": "arn:{p}:ec2:{r}:{a}:instance/{i}",
    "AWS::EC2::Subnet": "arn:{p}:ec2:{r}:{a}:subnet/{i}",
    "AWS::EC2::VPC": "arn:{p}:ec2:{r}:{a}:vpc/{i}",
    "AWS::EC2::SecurityGroup": "arn:{p}:ec2:{r}:{a}:security-group/{i}",
    "AWS::EC2::TransitGateway": "arn:{p}:ec2:{r}:{a}:transit-gateway/{i}",
    "AWS::EC2::VPCPeeringConnection": "arn:{p}:ec2:{r}:{a}:vpc-peering-connection/{i}",
    "AWS::RDS::DBInstance": "arn:{p}:rds:{r}:{a}:db:{i}",
    "AWS::Lambda::Function": "arn:{p}:lambda:{r}:{a}:function:{i}",
    "AWS::DynamoDB::Table": "arn:{p}:dynamodb:{r}:{a}:table/{i}",
    # ASSUMPTION: S3 bucket ARNs carry neither region nor account.
    "AWS::S3::Bucket": "arn:{p}:s3:::{i}",
}

# ASSUMPTION: for these types CloudFormation reports the ARN itself as the physical id.
_ARN_PASSTHROUGH_TYPES: frozenset[str] = frozenset(
    {
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        "AWS::ElasticLoadBalancingV2::Listener",
        "AWS::CloudFormation::Stack",
        "AWS::SNS::Topic",
    }
)

_TYPE_FROM_ARN: dict[tuple[str, str], str] = {
    ("ec2", "instance"): "AWS::EC2::Instance",
    ("ec2", "subnet"): "AWS::EC2::Subnet",
    ("ec2", "vpc"): "AWS::EC2::VPC",
    ("ec2", "security-group"): "AWS::EC2::SecurityGroup",
    ("ec2", "transit-gateway"): "AWS::EC2::TransitGateway",
    ("ec2", "vpc-peering-connection"): "AWS::EC2::VPCPeeringConnection",
    ("rds", "db"): "AWS::RDS::DBInstance",
    ("lambda", "function"): "AWS::Lambda::Function",
    ("dynamodb", "table"): "AWS::DynamoDB::Table",
    ("s3", ""): "AWS::S3::Bucket",
    ("elasticloadbalancing", "loadbalancer"): "AWS::ElasticLoadBalancingV2::LoadBalancer",
    ("elasticloadbalancing", "targetgroup"): "AWS::ElasticLoadBalancingV2::TargetGroup",
    ("elasticloadbalancing", "listener"): "AWS::ElasticLoadBalancingV2::Listener",
    ("cloudformation", "stack"): "AWS::CloudFormation::Stack",
    ("sns", ""): "AWS::SNS::Topic",
}


def arn_for(resource_type: str, physical_id: str, account_id: str, region: str) -> ArnResolution:
    """Build an ARN for a known resource type, or a deterministic placeholder.

    Unknown types (or passthrough types whose physical id is not an ARN) yield
    ``unresolved:{resource_type}:{physical_id}`` plus a note explaining why.
    """
    validate_account_id(account_id)
    if resource_type in _ARN_PASSTHROUGH_TYPES:
        if physical_id.startswith(f"arn:{PARTITION}:"):
            return ArnResolution(physical_id, None)
        return ArnResolution(
            f"{UNRESOLVED_PREFIX}{resource_type}:{physical_id}",
            f"physical id for {resource_type} is not an ARN; left unresolved",
        )
    template = _ARN_TEMPLATES.get(resource_type)
    if template is None:
        return ArnResolution(
            f"{UNRESOLVED_PREFIX}{resource_type}:{physical_id}",
            f"no ARN rule for resource type {resource_type}; left unresolved",
        )
    return ArnResolution(
        template.format(p=PARTITION, r=region, a=account_id, i=physical_id), None
    )


def key_for_arn(arn: str, default_account: str, default_region: str) -> ResourceKey:
    """Build a ``ResourceKey`` from an ARN (or ``unresolved:`` placeholder).

    The region comes from the ARN (``"global"`` when empty) and the account from the ARN
    (``default_account`` when empty, as for S3). Placeholders use both defaults.
    """
    if arn.startswith("arn:"):
        parts = arn.split(":", 5)
        if len(parts) == 6:
            region = parts[3] or GLOBAL_REGION
            account = parts[4] or default_account
            return ResourceKey(account_id=account, region=region, arn=arn)
    return ResourceKey(account_id=default_account, region=default_region, arn=arn)


def resource_type_from_arn(arn: str) -> str:
    """Map an ARN (or placeholder) to a CloudFormation-style resource type.

    Unknown services yield ``"{service}:unknown"``.
    """
    match = _UNRESOLVED_RE.match(arn)
    if match:
        return match.group(1)
    parts = arn.split(":", 5)
    if len(parts) != 6:
        return "unknown"
    service, resource = parts[2], parts[5]
    rtype = ""
    for sep in ("/", ":"):
        if sep in resource:
            rtype = resource.split(sep, 1)[0]
            break
    mapped = _TYPE_FROM_ARN.get((service, rtype))
    if mapped is None and service in {"s3", "sns"}:
        mapped = _TYPE_FROM_ARN.get((service, ""))
    return mapped or f"{service}:unknown"
