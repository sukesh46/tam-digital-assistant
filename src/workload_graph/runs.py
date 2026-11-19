"""Run registry and credential planners for the local page and the future service.

Status: partial
Purpose: start a run for accounts the operator typed, keep it findable by id, and choose how its
    accounts are read: ambient credential (one account, test pad) or assumed role (up to 20).
Inputs: account ids, region, an optional ``Ruleset``, an injected STS factory for tests.
Outputs: ``Runs``, ``AmbientPlanner``, ``AssumedRolePlanner``, the ``RunPlanner`` protocol.
Planned dependencies: credentials, fanout, jobs, live.
Notes:
    - ``prepare`` runs synchronously and may refuse (wrong account, bad mode). Refusals reach the
      caller before any job exists, so no half-started run is left behind.
    - Runs are held in memory and, when a ``RunStore`` is given, saved under ``out/runs/`` at
      start, when they settle and on every decision. A new registry loads them back.
    - ``threaded=False`` runs the job inline. Tests use it so they stay deterministic.
    - ``cost_review`` runs after approval with a client factory from the same planner, in us-east-1.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

import boto3

from workload_graph.costreview import CostCollect, collect_cost, run_cost_review
from workload_graph.credentials import (
    CLIENT_CONFIG,
    TAM_ROLE_NAME,
    AmbientCredentials,
    AssumedRoleCredentials,
    ClientFactory,
    ambient_client,
    validate_region,
)
from workload_graph.fanout import (
    DEFAULT_WORKERS,
    Collector,
    collector_for,
    normalise_account_ids,
    run_fanout,
)
from workload_graph.jobs import Job
from workload_graph.models import RawSnapshot
from workload_graph.ruleset import Ruleset
from workload_graph.runstore import RunStore

COST_REGION = "us-east-1"  # ASSUMPTION: the cost APIs' endpoint (cost assumption 10).


def _sts_for(region: str) -> Any:
    factory: Any = boto3.client
    return factory("sts", region_name=region, config=CLIENT_CONFIG)


class RunPlanner(Protocol):
    """Decides how the accounts of a run are read. Refuses with ``ValueError`` if it cannot."""

    def prepare(self, accounts: tuple[str, ...], region: str) -> Collector: ...

    def cost_factory(self, accounts: tuple[str, ...]) -> ClientFactory: ...


class AmbientPlanner:
    """Local test mode: the ambient credential, for exactly one account, verified with STS."""

    def __init__(self, sts_factory: Callable[[str], Any] = _sts_for,
                 ruleset: Ruleset | None = None) -> None:
        self._sts_factory = sts_factory
        self._ruleset = ruleset or Ruleset()

    def prepare(self, accounts: tuple[str, ...], region: str) -> Collector:
        credentials = AmbientCredentials(accounts, region, sts_factory=self._sts_factory)
        credentials.verify()
        return collector_for(ambient_client, region, self._ruleset, datetime.now(UTC))

    def cost_factory(self, accounts: tuple[str, ...]) -> ClientFactory:
        """A cost API client factory, once the credential is checked against the account."""
        credentials = AmbientCredentials(accounts, COST_REGION, sts_factory=self._sts_factory)
        credentials.verify()
        return ambient_client


class AssumedRolePlanner:
    """Deployed mode: AssumeRole into ``TamReadOnly`` in each approved member account."""

    role_name = TAM_ROLE_NAME

    def __init__(self, external_id: str, sts_factory: Callable[[str], Any] = _sts_for,
                 ruleset: Ruleset | None = None) -> None:
        if not external_id:
            raise ValueError("an external id is required for the assumed-role mode")
        self._external_id = external_id
        self._sts_factory = sts_factory
        self._ruleset = ruleset or Ruleset()

    def prepare(self, accounts: tuple[str, ...], region: str) -> Collector:
        validate_region(region)
        credentials = AssumedRoleCredentials(
            accounts, (region,), self._sts_factory(region),
            external_id=self._external_id, purpose=f"run-{uuid.uuid4().hex[:12]}",
        )
        factory: ClientFactory = credentials.client_factory()
        return collector_for(factory, region, self._ruleset, datetime.now(UTC))

    def cost_factory(self, accounts: tuple[str, ...]) -> ClientFactory:
        """A client factory for the cost APIs through the same role (needs the cost actions)."""
        credentials = AssumedRoleCredentials(
            accounts, (COST_REGION,), self._sts_factory(COST_REGION),
            external_id=self._external_id, purpose=f"cost-{uuid.uuid4().hex[:12]}",
        )
        return credentials.client_factory()


class Runs:
    """In-memory registry of runs. Each run is one ``Job``."""

    def __init__(self, planner: RunPlanner, *, threaded: bool = True,
                 workers: int = DEFAULT_WORKERS, ruleset: Ruleset | None = None,
                 store: RunStore | None = None, cost_collect: CostCollect = collect_cost,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._planner = planner
        self._cost_collect = cost_collect
        self._clock = clock
        self._threaded = threaded
        self._workers = workers
        self._ruleset = ruleset or Ruleset()
        self._store = store
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        if store is not None:
            for job in store.load_all():
                self._jobs[job.job_id] = job

    def start(self, account_ids: Iterable[str], region: str) -> Job:
        """Validate, plan, register and run. ``ValueError`` is raised before any job exists."""
        accounts = normalise_account_ids(account_ids)
        validate_region(region)
        collect = self._planner.prepare(accounts, region)
        job = Job(uuid.uuid4().hex, accounts, region=region)
        with self._lock:
            self._jobs[job.job_id] = job
        self._save(job)

        store = self._store

        def keep(snapshots: list[RawSnapshot]) -> None:
            if store is not None:
                store.save_snapshots(job.job_id, "graph", snapshots)

        def work() -> None:
            try:
                run_fanout(job, accounts, region, collect, self._ruleset, workers=self._workers,
                           keep=keep)
            finally:
                self._save(job)

        if self._threaded:
            threading.Thread(target=work, name=f"run-{job.job_id[:8]}", daemon=True).start()
        else:
            work()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def decide(self, job_id: str, workload_id: str, decision: str) -> Job:
        """Record approve or reject and save it. ``KeyError`` for an unknown run."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.decide(workload_id, decision)
        self._save(job)
        return job

    def cost_review(self, job_id: str) -> Job:
        """Start the cost review of a reviewed run. Refusals raise before anything starts."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.approved():
            raise ValueError("approve at least one workload first")
        factory = self._planner.cost_factory(job.accounts)
        job.begin_cost()
        self._save(job)
        moment = self._clock()

        def work() -> None:
            try:
                run_cost_review(job, factory, self._store, as_of=moment.date(),
                                collected_at=moment, workload_ruleset=self._ruleset,
                                collect=self._cost_collect)
            finally:
                self._save(job)

        if self._threaded:
            threading.Thread(target=work, name=f"cost-{job_id[:8]}", daemon=True).start()
        else:
            work()
        return job

    def report(self, job_id: str) -> str | None:
        """The saved findings report of a run, as JSON text."""
        if self._store is None or self.get(job_id) is None:
            return None
        return self._store.load_report(job_id)

    def list(self) -> list[dict[str, Any]]:
        """One summary per run, newest first."""
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: (j.created_at, j.job_id), reverse=True)
        summaries: list[dict[str, Any]] = []
        for job in jobs:
            result = job.result
            decisions = job.decisions
            summaries.append({
                "job_id": job.job_id,
                "accounts": list(job.accounts),
                "region": job.region,
                "created_at": job.created_at.isoformat(),
                "state": job.state.value,
                "workloads": 0 if result is None else len(result.workloads),
                "approved": sum(1 for d in decisions.values() if d == "approve"),
                "rejected": sum(1 for d in decisions.values() if d == "reject"),
                "cost_state": job.cost_state,
            })
        return summaries

    def _save(self, job: Job) -> None:
        if self._store is not None:
            self._store.save(job)
