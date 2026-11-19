"""Read-only collection for one account, and the shared per-account collection step.

Status: partial
Purpose: collect the four sources for one account with a client factory, then build. The
    single-account UI path uses the ambient credential; the multi-account fan-out reuses
    ``collect_snapshots`` with an assumed-role factory.
Inputs: account id and region. The account id is supplied by the operator; it is not stored
    in the project.
Outputs: ``BuildResult`` from ``build_account``; ``list[RawSnapshot]`` from ``collect_snapshots``.
Planned dependencies: boto3, collectors, pipeline, credentials.
Notes:
    - ``build_account`` collects only when STS reports the same account the operator entered.
    - No role is assumed and no IAM user is created on this path.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from workload_graph.collectors import COLLECTORS
from workload_graph.collectors.base import CollectionScope
from workload_graph.credentials import (
    AmbientCredentials,
    ClientFactory,
    ambient_client,
    validate_region,
)
from workload_graph.ids import validate_account_id
from workload_graph.models import BuildResult, RawSnapshot, Workspace
from workload_graph.models.snapshot import SourceName
from workload_graph.normalisers import normalise
from workload_graph.pipeline import run_snapshots
from workload_graph.ruleset import Ruleset

_SEED_CAP = 40

# (kind, detail): told when a source starts and finishes. Details are codes, never payloads.
Progress = Callable[[str, str], None]


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


def collect_snapshots(client_factory: ClientFactory, account_id: str, region: str,
                      ruleset: Ruleset, moment: datetime,
                      progress: Progress | None = None) -> list[RawSnapshot]:
    """Collect the four sources for one account. CloudTrail is seeded from the first three.

    ``progress(kind, detail)`` is told when each source starts and finishes.
    """

    def run(source: SourceName, scope: CollectionScope) -> RawSnapshot:
        if progress is not None:
            progress("source_started", source)
        snapshot = COLLECTORS[source]().collect(client_factory, account_id, region, scope)
        if progress is not None:
            progress("source_done", f"{source}:{snapshot.status}")
        return snapshot

    scope = CollectionScope.from_ruleset(ruleset, moment)
    snapshots = [run(source, scope) for source in ("tags", "stacks", "topology")]
    seeded = CollectionScope.from_ruleset(ruleset, moment, _seeds(snapshots, ruleset))
    snapshots.append(run("cloudtrail", seeded))
    return snapshots


def build_account(account_id: str, region: str, ruleset: Ruleset | None = None,
                  now: datetime | None = None) -> BuildResult:
    """Collect the typed account and build candidate workloads.

    Refuses to call any other account than the one STS says the current credentials are for.
    """
    validate_account_id(account_id)
    validate_region(region)
    AmbientCredentials((account_id,), region).verify()
    rules = ruleset or Ruleset()
    moment = now or datetime.now(UTC)
    snapshots = collect_snapshots(ambient_client, account_id, region, rules, moment)
    workspace = Workspace(
        workspace_id="ui", customer="example-customer",
        approved_account_ids=[account_id], regions=[region],
    )
    return run_snapshots(snapshots, rules, workspace)
