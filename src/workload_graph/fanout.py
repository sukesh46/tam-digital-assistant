"""Multi-account run: intake, bounded parallel collection, then one joined build.

Status: partial
Purpose: accept up to 20 member accounts, collect each in its own worker, then merge every
    snapshot into one store and build once. Cross-account references resolve after the merge.
Inputs: account ids (validated, de-duplicated, sorted), one region, a per-account ``Collector``,
    a ruleset, a worker count, and a ``Job`` that records progress.
Outputs: ``BuildResult`` (also stored on the job), or ``None`` if the joined build failed.
Planned dependencies: concurrent.futures, botocore exceptions, live, pipeline, jobs, credentials.
Notes:
    - Per-account failure is isolated. A denied or broken account is noted and the others still
      build. The joined build's approved list still names every requested account.
    - Snapshots join in account-id order, not completion order, so the result does not depend on
      thread scheduling.
    - Event details are codes only; exception messages are never copied into events.
    - Workers are bounded by ``workers``. Nothing here retries.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from workload_graph.auth.session import OutOfScopeAccountError
from workload_graph.credentials import TAM_ROLE_NAME, ClientFactory, validate_region
from workload_graph.ids import validate_account_id
from workload_graph.jobs import Job, JobState
from workload_graph.live import Progress, collect_snapshots
from workload_graph.models import BuildResult, RawSnapshot, Workspace
from workload_graph.pipeline import run_snapshots
from workload_graph.ruleset import Ruleset

MAX_ACCOUNTS = 20
DEFAULT_WORKERS = 5

# account_id -> the snapshots collected for it. Raises on failure; run_fanout isolates that.
Collector = Callable[[str, Progress], list[RawSnapshot]]


def normalise_account_ids(raw: Iterable[str]) -> tuple[str, ...]:
    """Validate, de-duplicate and sort account ids. Refuses empty lists and more than 20."""
    items = [str(item).strip() for item in raw]
    if not items:
        raise ValueError("at least one account id is required")
    for item in items:
        validate_account_id(item)
    unique = tuple(sorted(set(items)))
    if len(unique) > MAX_ACCOUNTS:
        raise ValueError(f"at most {MAX_ACCOUNTS} accounts per run")
    return unique


def collector_for(client_factory: ClientFactory, region: str, ruleset: Ruleset,
                  moment: datetime) -> Collector:
    """A ``Collector`` that runs the four sources for one account with ``client_factory``."""

    def collect(account_id: str, progress: Progress) -> list[RawSnapshot]:
        return collect_snapshots(client_factory, account_id, region, ruleset, moment, progress)

    return collect


@dataclass(frozen=True)
class AccountOutcome:
    account_id: str
    snapshots: tuple[RawSnapshot, ...]
    failure: str | None


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        code: Any = exc.response.get("Error", {}).get("Code", "ClientError")
        return str(code)
    if isinstance(exc, OutOfScopeAccountError):
        return "OutOfScope"
    return type(exc).__name__


def _collect_one(account_id: str, collect: Collector, job: Job) -> AccountOutcome:
    def progress(kind: str, detail: str) -> None:
        job.emit(kind, account_id, detail)

    try:
        return AccountOutcome(account_id, tuple(collect(account_id, progress)), None)
    except Exception as exc:  # isolation: one account must not stop the others
        return AccountOutcome(account_id, (), _failure_code(exc))


def _source_summary(snapshots: tuple[RawSnapshot, ...]) -> str:
    return ",".join(f"{snapshot.source}:{snapshot.status}" for snapshot in snapshots)


def _run_notes(accounts: tuple[str, ...], outcomes: dict[str, AccountOutcome]) -> list[str]:
    """Notes for the run: refused accounts, then each collector note with its error code."""
    notes: list[str] = []
    for account_id in accounts:
        outcome = outcomes[account_id]
        if outcome.failure is not None:
            notes.append(f"account {account_id} was not collected: {outcome.failure}")
        for snapshot in outcome.snapshots:
            for note in snapshot.notes:
                notes.append(f"account {account_id} {snapshot.source}: {note}")
    return notes


def _run(job: Job, accounts: tuple[str, ...], region: str, collect: Collector,
         ruleset: Ruleset, workers: int, customer: str) -> BuildResult:
    job.transition(JobState.RUNNING, "running", f"{len(accounts)} accounts")
    for account_id in accounts:
        job.emit("account_started", account_id)

    outcomes: dict[str, AccountOutcome] = {}

    def record(outcome: AccountOutcome) -> None:
        outcomes[outcome.account_id] = outcome
        if outcome.failure is None:
            job.emit("account_collected", outcome.account_id, _source_summary(outcome.snapshots))
        else:
            job.emit("account_failed", outcome.account_id, outcome.failure)

    pool_size = max(1, min(workers, len(accounts)))
    if pool_size == 1:
        # One account, or one worker: nothing to run concurrently, so no pool and no threads.
        for account_id in accounts:
            record(_collect_one(account_id, collect, job))
    else:
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            futures = [pool.submit(_collect_one, account_id, collect, job)
                       for account_id in accounts]
            for future in as_completed(futures):
                record(future.result())

    snapshots = [s for account_id in accounts for s in outcomes[account_id].snapshots]
    notes = tuple(_run_notes(accounts, outcomes))

    job.transition(JobState.BUILDING, "building")
    workspace = Workspace(
        workspace_id=job.job_id, customer=customer,
        approved_account_ids=list(accounts), regions=[region], role_name=TAM_ROLE_NAME,
    )
    result = run_snapshots(snapshots, ruleset, workspace, notes)
    job.set_result(result)
    job.transition(JobState.AWAITING_REVIEW, "awaiting_review",
                   f"{len(result.workloads)} workloads")
    return result


def run_fanout(job: Job, account_ids: Iterable[str], region: str, collect: Collector,
               ruleset: Ruleset, workers: int = DEFAULT_WORKERS,
               customer: str = "unnamed-customer") -> BuildResult | None:
    """Collect every account in parallel, then build once over the merged snapshots.

    Returns the result, or ``None`` if the joined build raised (the job is then ``FAILED``).
    Bad input raises ``ValueError`` before any work starts.
    """
    accounts = normalise_account_ids(account_ids)
    validate_region(region)
    try:
        return _run(job, accounts, region, collect, ruleset, workers, customer)
    except Exception as exc:
        job.transition(JobState.FAILED, "failed", type(exc).__name__)
        return None
