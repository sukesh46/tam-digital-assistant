"""Read-only collection for one account the operator typed in.

Status: partial
Purpose: collect the four sources with the credentials already in the environment, then build.
Inputs: account id and region. The account id is supplied by the operator; it is not stored
    in the project.
Outputs: ``BuildResult``.
Planned dependencies: boto3, collectors, pipeline.
Notes:
    - Collection runs only when STS reports the same account the operator entered.
    - No role is assumed and no IAM user is created. The ambient credential is used as-is.
    - # SKELETON: this path is the test-pad flow. Approved-role assume remains unwired.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import boto3

from workload_graph.collectors import COLLECTORS
from workload_graph.collectors.base import CollectionScope
from workload_graph.ids import validate_account_id
from workload_graph.models import BuildResult, RawSnapshot, Workspace
from workload_graph.normalisers import normalise
from workload_graph.pipeline import run_snapshots
from workload_graph.ruleset import Ruleset

_REGION = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
_SEED_CAP = 40


def _client(service: str, _account_id: str, region: str) -> Any:
    """Ambient boto3 client. Any: service client types differ, and the name is dynamic."""
    factory: Any = boto3.client
    return factory(service, region_name=region)


def _seeds(snapshots: list[RawSnapshot], ruleset: Ruleset) -> tuple[str, ...]:
    found: set[str] = set()
    for snapshot in snapshots:
        for resource in normalise(snapshot, ruleset).resources:
            if resource.name:
                found.add(resource.name)
            arn = resource.key.arn
            if arn.startswith("arn:aws:s3:::"):
                found.add(arn.removeprefix("arn:aws:s3:::"))
            else:
                found.add(arn.rsplit(":", 1)[-1].rsplit("/", 1)[-1])
    return tuple(sorted(found)[:_SEED_CAP])


def build_account(account_id: str, region: str, ruleset: Ruleset | None = None,
                  now: datetime | None = None) -> BuildResult:
    """Collect the typed account and build candidate workloads.

    Refuses to call any other account than the one STS says the current credentials are for.
    """
    validate_account_id(account_id)
    if _REGION.fullmatch(region) is None:
        raise ValueError("region must look like ap-south-1")
    caller = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    if caller != account_id:
        raise ValueError("the credentials in this environment are for a different account")
    rules = ruleset or Ruleset()
    moment = now or datetime.now(UTC)
    scope = CollectionScope.from_ruleset(rules, moment)
    snapshots = [
        COLLECTORS[source]().collect(_client, account_id, region, scope)
        for source in ("tags", "stacks", "topology")
    ]
    seeded = CollectionScope.from_ruleset(rules, moment, _seeds(snapshots, rules))
    snapshots.append(COLLECTORS["cloudtrail"]().collect(_client, account_id, region, seeded))
    workspace = Workspace(
        workspace_id="ui", customer="example-customer",
        approved_account_ids=[account_id], regions=[region],
    )
    return run_snapshots(snapshots, rules, workspace)
