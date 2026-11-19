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
    - Runs are held in memory. Local persistence under ``out/runs/`` is a later step.
    - ``threaded=False`` runs the job inline. Tests use it so they stay deterministic.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

import boto3

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
from workload_graph.ruleset import Ruleset


def _sts_for(region: str) -> Any:
    factory: Any = boto3.client
    return factory("sts", region_name=region, config=CLIENT_CONFIG)


class RunPlanner(Protocol):
    """Decides how the accounts of a run are read. Refuses with ``ValueError`` if it cannot."""

    def prepare(self, accounts: tuple[str, ...], region: str) -> Collector: ...


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


class Runs:
    """In-memory registry of runs. Each run is one ``Job``."""

    def __init__(self, planner: RunPlanner, *, threaded: bool = True,
                 workers: int = DEFAULT_WORKERS, ruleset: Ruleset | None = None) -> None:
        self._planner = planner
        self._threaded = threaded
        self._workers = workers
        self._ruleset = ruleset or Ruleset()
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def start(self, account_ids: Iterable[str], region: str) -> Job:
        """Validate, plan, register and run. ``ValueError`` is raised before any job exists."""
        accounts = normalise_account_ids(account_ids)
        validate_region(region)
        collect = self._planner.prepare(accounts, region)
        job = Job(uuid.uuid4().hex, accounts)
        with self._lock:
            self._jobs[job.job_id] = job

        def work() -> None:
            run_fanout(job, accounts, region, collect, self._ruleset, workers=self._workers)

        if self._threaded:
            threading.Thread(target=work, name=f"run-{job.job_id[:8]}", daemon=True).start()
        else:
            work()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
