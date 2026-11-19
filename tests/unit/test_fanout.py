"""Step 15: multi-account fan-out, credential modes and the job event stream.

Offline only. STS is stubbed with botocore's Stubber; collectors are injected as fakes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C, REGION, scenario_snapshots

from workload_graph.auth.session import OutOfScopeAccountError
from workload_graph.credentials import TAM_ROLE_NAME, AmbientCredentials, AssumedRoleCredentials
from workload_graph.fanout import (
    MAX_ACCOUNTS,
    normalise_account_ids,
    run_fanout,
)
from workload_graph.jobs import Job, JobState
from workload_graph.models import RawSnapshot
from workload_graph.ruleset import Ruleset

FAKE_KEY = "testing-not-a-key"
FAKE_SECRET = "testing-not-a-secret"


def _sts_client() -> tuple[Any, Stubber]:
    client = boto3.client("sts", region_name=REGION, aws_access_key_id=FAKE_KEY,
                          aws_secret_access_key=FAKE_SECRET)
    stubber = Stubber(client)
    stubber.activate()
    return client, stubber


def _twenty_one_accounts() -> list[str]:
    return [f"{n:012d}" for n in range(100_000_000_001, 100_000_000_022)]


# --- account intake -------------------------------------------------------------------------

def test_account_ids_are_deduplicated_and_sorted() -> None:
    assert normalise_account_ids([ACCT_B, ACCT_A, ACCT_B]) == (ACCT_A, ACCT_B)


def test_twenty_accounts_are_accepted_and_twenty_one_are_refused() -> None:
    accounts = _twenty_one_accounts()
    assert len(normalise_account_ids(accounts[:MAX_ACCOUNTS])) == MAX_ACCOUNTS
    with pytest.raises(ValueError, match="at most 20"):
        normalise_account_ids(accounts)


@pytest.mark.parametrize("raw", [[], ["nope"], ["1234"], [ACCT_A, "12345678901x"]])
def test_empty_or_malformed_account_lists_are_refused(raw: list[str]) -> None:
    with pytest.raises(ValueError):
        normalise_account_ids(raw)


# --- job event stream -----------------------------------------------------------------------

def test_job_events_are_sequenced_and_the_job_settles_once_it_has_a_result() -> None:
    job = Job("job-1", (ACCT_A,))
    assert job.state is JobState.QUEUED
    job.transition(JobState.RUNNING, "running")
    job.emit("account_started", ACCT_A)
    job.transition(JobState.AWAITING_REVIEW, "awaiting_review", "0 workloads")
    events = job.events_after(0)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.kind for e in events] == ["running", "account_started", "awaiting_review"]
    assert job.events_after(2)[0].kind == "awaiting_review"
    assert job.is_settled


def test_wait_returns_as_soon_as_another_thread_emits() -> None:
    job = Job("job-2", (ACCT_A,))

    def later() -> None:
        time.sleep(0.05)
        job.emit("account_collected", ACCT_A, "tags:ok")

    thread = threading.Thread(target=later)
    thread.start()
    started = time.monotonic()
    events = job.wait_for_events(after=0, timeout=5.0)
    thread.join()
    assert [e.kind for e in events] == ["account_collected"]
    assert time.monotonic() - started < 4.0


def test_wait_times_out_with_no_new_events() -> None:
    job = Job("job-3", (ACCT_A,))
    assert job.wait_for_events(after=0, timeout=0.01) == []


# --- credential modes -----------------------------------------------------------------------

def test_ambient_mode_serves_exactly_one_account() -> None:
    with pytest.raises(ValueError, match="one account"):
        AmbientCredentials((ACCT_A, ACCT_B), REGION)


def test_ambient_mode_refuses_when_sts_reports_another_account() -> None:
    def sts_factory(_region: str) -> Any:
        class Fake:
            def get_caller_identity(self) -> dict[str, str]:
                return {"Account": ACCT_B}
        return Fake()

    creds = AmbientCredentials((ACCT_A,), REGION, sts_factory=sts_factory)
    with pytest.raises(ValueError, match="different account"):
        creds.verify()


def test_ambient_mode_accepts_matching_account() -> None:
    def sts_factory(_region: str) -> Any:
        class Fake:
            def get_caller_identity(self) -> dict[str, str]:
                return {"Account": ACCT_A}
        return Fake()

    AmbientCredentials((ACCT_A,), REGION, sts_factory=sts_factory).verify()


def test_assumed_role_uses_the_tam_role_name_and_the_external_id() -> None:
    client, stubber = _sts_client()
    expiration = "2026-01-15T13:00:00Z"
    stubber.add_response(
        "assume_role",
        {"Credentials": {"AccessKeyId": "ASIAFAKEFAKEFAKE0001",
                         "SecretAccessKey": "fake/secret/value/for/tests/only/0001",
                         "SessionToken": "fake-session-token-for-tests-only-0001",
                         "Expiration": expiration}},
        {"RoleArn": f"arn:aws:iam::{ACCT_A}:role/{TAM_ROLE_NAME}",
         "RoleSessionName": ANY, "DurationSeconds": 3600,
         "ExternalId": "example-external-id"},
    )
    creds = AssumedRoleCredentials(
        (ACCT_A,), (REGION,), client, external_id="example-external-id", purpose="job-1",
    )
    client = creds.client_factory()("resourcegroupstaggingapi", ACCT_A, REGION)
    stubber.assert_no_pending_responses()
    assert client.meta.region_name == REGION
    assert TAM_ROLE_NAME == "TamReadOnly"


def test_assumed_role_refuses_an_unapproved_account_before_any_sts_call() -> None:
    client, stubber = _sts_client()
    creds = AssumedRoleCredentials(
        (ACCT_A,), (REGION,), client, external_id="example-external-id", purpose="job-2",
    )
    with pytest.raises(OutOfScopeAccountError):
        creds.client_factory()("resourcegroupstaggingapi", ACCT_C, REGION)
    stubber.assert_no_pending_responses()


def test_assumed_role_requires_an_external_id() -> None:
    client, _ = _sts_client()
    with pytest.raises(ValueError, match="external id"):
        AssumedRoleCredentials((ACCT_A,), (REGION,), client, external_id="", purpose="job-3")


# --- fan-out and join -----------------------------------------------------------------------

def _fixture_collect(
    accounts_with_data: dict[str, list[RawSnapshot]],
    delays: dict[str, float] | None = None,
) -> Any:
    def collect(account_id: str) -> list[RawSnapshot]:
        if delays and account_id in delays:
            time.sleep(delays[account_id])
        return list(accounts_with_data.get(account_id, []))
    return collect


def _run(collect: Any, accounts: tuple[str, ...], workers: int = 3) -> tuple[Job, Any]:
    job = Job("job-run", accounts)
    result = run_fanout(job, accounts, REGION, collect, Ruleset(), workers=workers,
                        customer="example-customer")
    return job, result


def test_fanout_result_does_not_depend_on_worker_count_or_completion_order() -> None:
    data = {ACCT_A: scenario_snapshots("s1")}
    accounts = (ACCT_A, ACCT_B, ACCT_C)
    slow_first = {ACCT_A: 0.06, ACCT_B: 0.03, ACCT_C: 0.0}
    _, serial = _run(_fixture_collect(data), accounts, workers=1)
    _, parallel = _run(_fixture_collect(data, slow_first), accounts, workers=3)
    assert serial is not None and parallel is not None
    assert serial.to_json() == parallel.to_json()


def test_workers_are_bounded() -> None:
    lock = threading.Lock()
    active = 0
    peak = 0

    def collect(_account_id: str) -> list[RawSnapshot]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return []

    accounts = tuple(_twenty_one_accounts()[:6])
    _run(collect, accounts, workers=2)
    assert peak <= 2


def test_one_failing_account_does_not_stop_the_others() -> None:
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "AssumeRole")
    data = {ACCT_A: scenario_snapshots("s1")}

    def collect(account_id: str) -> list[RawSnapshot]:
        if account_id == ACCT_B:
            raise denied
        return _fixture_collect(data)(account_id)

    job, result = _run(collect, (ACCT_A, ACCT_B))
    assert result is not None
    assert job.state is JobState.AWAITING_REVIEW
    assert result.workloads
    failures = [e for e in job.events_after(0) if e.kind == "account_failed"]
    assert [(e.account_id, e.detail) for e in failures] == [(ACCT_B, "AccessDenied")]
    assert any(ACCT_B in note for note in result.notes)


def test_an_unexpected_error_in_one_account_is_isolated_and_not_leaked() -> None:
    def collect(account_id: str) -> list[RawSnapshot]:
        raise RuntimeError(f"secret detail about {account_id}")

    job, result = _run(collect, (ACCT_A,))
    assert result is not None
    assert job.state is JobState.AWAITING_REVIEW
    detail = [e.detail for e in job.events_after(0) if e.kind == "account_failed"]
    assert detail == ["RuntimeError"]
    assert all(ACCT_A not in e.detail for e in job.events_after(0))


def test_a_failure_in_the_joined_build_fails_the_job_without_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_build(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"build broke on {ACCT_A}")

    monkeypatch.setattr("workload_graph.fanout.run_snapshots", broken_build)
    data = {ACCT_A: scenario_snapshots("s1")}
    job, result = _run(_fixture_collect(data), (ACCT_A,))
    assert result is None
    assert job.state is JobState.FAILED
    failed = [e for e in job.events_after(0) if e.kind == "failed"]
    assert [e.detail for e in failed] == ["RuntimeError"]
    assert all(ACCT_A not in e.detail for e in job.events_after(0))


def test_a_collector_note_carries_its_error_code_into_the_run_notes() -> None:
    snapshots = scenario_snapshots("s1")
    noted = snapshots[0].model_copy(
        update={"notes": ["GetResources: failed (ThrottlingException)"]})

    def collect(account_id: str) -> list[RawSnapshot]:
        return [noted, *snapshots[1:]] if account_id == ACCT_A else []

    _, result = _run(collect, (ACCT_A,))
    assert result is not None
    assert any(
        note == f"account {ACCT_A} {noted.source}: GetResources: failed (ThrottlingException)"
        for note in result.notes
    )


def test_the_joined_build_is_scoped_to_every_requested_account() -> None:
    data = {ACCT_A: scenario_snapshots("s1")}
    _, result = _run(_fixture_collect(data), (ACCT_A, ACCT_B))
    assert result is not None
    assert all(w.members for w in result.workloads)
    assert {m.key.account_id for w in result.workloads for m in w.members} == {ACCT_A}
