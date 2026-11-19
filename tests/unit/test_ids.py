"""Step 2: identity tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from workload_graph.ids import (
    ResourceKey,
    arn_for,
    key_for_arn,
    resource_type_from_arn,
    validate_account_id,
)

ACCT = "111111111111"
REGION = "eu-west-1"


@pytest.mark.parametrize("bad", ["", "12345", "1111111111111", "11111111111a", " 111111111111"])
def test_account_id_rejects_non_twelve_digit_input(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_account_id(bad)


def test_account_id_accepts_twelve_digits() -> None:
    assert validate_account_id(ACCT) == ACCT


def test_resource_key_rejects_bad_account_id() -> None:
    with pytest.raises(ValidationError):
        ResourceKey(account_id="123", region=REGION, arn="arn:aws:ec2:x")


def test_resource_keys_with_same_fields_are_equal_and_hash_equal() -> None:
    a = ResourceKey(account_id=ACCT, region=REGION, arn="arn:aws:ec2:eu-west-1:111111111111:vpc/v")
    b = ResourceKey(account_id=ACCT, region=REGION, arn="arn:aws:ec2:eu-west-1:111111111111:vpc/v")
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_key_str_has_account_region_arn_format() -> None:
    key = ResourceKey(account_id=ACCT, region="global", arn="arn:aws:s3:::bucket-a")
    assert key.key_str() == "111111111111|global|arn:aws:s3:::bucket-a"


def test_keys_sort_lexicographically_by_key_str() -> None:
    keys = [
        ResourceKey(account_id="222222222222", region="a", arn="x"),
        ResourceKey(account_id=ACCT, region="b", arn="a"),
        ResourceKey(account_id=ACCT, region="a", arn="z"),
    ]
    ordered = sorted(keys)
    assert [k.key_str() for k in ordered] == sorted(k.key_str() for k in keys)
    assert sorted(reversed(keys)) == ordered


@pytest.mark.parametrize(
    ("resource_type", "physical_id", "expected"),
    [
        ("AWS::EC2::Instance", "i-0aaa", "arn:aws:ec2:eu-west-1:111111111111:instance/i-0aaa"),
        (
            "AWS::EC2::Subnet",
            "subnet-0aaa",
            "arn:aws:ec2:eu-west-1:111111111111:subnet/subnet-0aaa",
        ),
        ("AWS::EC2::VPC", "vpc-0aaa", "arn:aws:ec2:eu-west-1:111111111111:vpc/vpc-0aaa"),
        (
            "AWS::EC2::SecurityGroup",
            "sg-0aaa",
            "arn:aws:ec2:eu-west-1:111111111111:security-group/sg-0aaa",
        ),
        (
            "AWS::EC2::TransitGateway",
            "tgw-0aaa",
            "arn:aws:ec2:eu-west-1:111111111111:transit-gateway/tgw-0aaa",
        ),
        ("AWS::RDS::DBInstance", "orders-db", "arn:aws:rds:eu-west-1:111111111111:db:orders-db"),
        (
            "AWS::Lambda::Function",
            "orders-fn",
            "arn:aws:lambda:eu-west-1:111111111111:function:orders-fn",
        ),
        (
            "AWS::DynamoDB::Table",
            "orders-tbl",
            "arn:aws:dynamodb:eu-west-1:111111111111:table/orders-tbl",
        ),
        ("AWS::S3::Bucket", "orders-bucket", "arn:aws:s3:::orders-bucket"),
        (
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
            "arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/web/abc",
            "arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/web/abc",
        ),
        (
            "AWS::ElasticLoadBalancingV2::TargetGroup",
            "arn:aws:elasticloadbalancing:eu-west-1:111111111111:targetgroup/web/abc",
            "arn:aws:elasticloadbalancing:eu-west-1:111111111111:targetgroup/web/abc",
        ),
    ],
)
def test_arn_for_known_types_produces_exact_arns(
    resource_type: str, physical_id: str, expected: str
) -> None:
    result = arn_for(resource_type, physical_id, ACCT, REGION)
    assert result.arn == expected
    assert result.note is None


def test_arn_for_unknown_type_yields_unresolved_placeholder_and_note() -> None:
    result = arn_for("AWS::Foo::Bar", "thing-1", ACCT, REGION)
    assert result.arn == "unresolved:AWS::Foo::Bar:thing-1"
    assert result.note is not None
    assert "AWS::Foo::Bar" in result.note


def test_arn_for_elbv2_with_non_arn_physical_id_is_unresolved() -> None:
    result = arn_for("AWS::ElasticLoadBalancingV2::LoadBalancer", "web", ACCT, REGION)
    assert result.arn.startswith("unresolved:")
    assert result.note is not None


def test_arn_for_rejects_bad_account() -> None:
    with pytest.raises(ValueError):
        arn_for("AWS::EC2::VPC", "vpc-1", "12", REGION)


def test_key_for_arn_uses_arn_region_and_account() -> None:
    key = key_for_arn("arn:aws:ec2:us-east-1:222222222222:vpc/vpc-1", default_account=ACCT,
                      default_region=REGION)
    assert key == ResourceKey(
        account_id="222222222222", region="us-east-1",
        arn="arn:aws:ec2:us-east-1:222222222222:vpc/vpc-1",
    )


def test_key_for_arn_uses_global_region_and_default_account_for_s3() -> None:
    key = key_for_arn("arn:aws:s3:::bucket-a", default_account=ACCT, default_region=REGION)
    assert key == ResourceKey(account_id=ACCT, region="global", arn="arn:aws:s3:::bucket-a")


def test_key_for_unresolved_placeholder_uses_defaults() -> None:
    key = key_for_arn("unresolved:AWS::Foo::Bar:x", default_account=ACCT, default_region=REGION)
    assert key.region == REGION
    assert key.account_id == ACCT


@pytest.mark.parametrize(
    ("arn", "expected"),
    [
        ("arn:aws:ec2:eu-west-1:111111111111:instance/i-1", "AWS::EC2::Instance"),
        ("arn:aws:rds:eu-west-1:111111111111:db:orders", "AWS::RDS::DBInstance"),
        ("arn:aws:s3:::bucket", "AWS::S3::Bucket"),
        (
            "arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/x/1",
            "AWS::ElasticLoadBalancingV2::LoadBalancer",
        ),
        ("arn:aws:sqs:eu-west-1:111111111111:queue-a", "sqs:unknown"),
        ("unresolved:AWS::Foo::Bar:x", "AWS::Foo::Bar"),
    ],
)
def test_resource_type_from_arn_maps_known_types(arn: str, expected: str) -> None:
    assert resource_type_from_arn(arn) == expected
