"""Step 16: run registry, planners, review decisions and the JSON routes. All offline.

The planner and the collector are fakes, so no socket is opened. The routes are plain functions
that return ``(status, body)``, so the tests do not need an HTTP server.
"""

from __future__ import annotations

from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from wg_test_helpers import ACCT_A, ACCT_B, ACCT_C, REGION, scenario_snapshots

from workload_graph.credentials import TAM_ROLE_NAME
from workload_graph.jobs import Job, JobState
from workload_graph.models import RawSnapshot
from workload_graph.runs import AmbientPlanner, AssumedRolePlanner, Runs
from workload_graph.ui_server import create_job, poll_job, review_workload


def _fixture_planner() -> Any:
    """A planner whose collector serves the s1 fixtures for ACCT_A and nothing for others."""
    snapshots = scenario_snapshots("s1")

    class Fixture:
        def prepare(self, accounts: tuple[str, ...], region: str) -> Any:
            def collect(account_id: str, _progress: Any) -> list[RawSnapshot]:
                return list(snapshots) if account_id == ACCT_A else []
            return collect

    return Fixture()


def _runs(planner: Any = None) -> Runs:
    return Runs(planner or _fixture_planner(), threaded=False)


# --- decisions -----------------------------------------------------------------------------

def test_decisions_are_allowed_only_after_the_build_and_only_for_known_workloads() -> None:
    runs = _runs()
    job = runs.start([ACCT_A], REGION)
    assert job.state is JobState.AWAITING_REVIEW
    workload_id = job.result.workloads[0].workload_id  # type: ignore[union-attr]
    job.decide(workload_id, "approve")
    assert job.decisions == {workload_id: "approve"}
    job.decide(workload_id, "reject")
    assert job.decisions == {workload_id: "reject"}
    with pytest.raises(ValueError, match="unknown workload"):
        job.decide("not-a-workload", "approve")
    with pytest.raises(ValueError, match="approve or reject"):
        job.decide(workload_id, "maybe")


def test_a_decision_before_the_build_settles_is_refused() -> None:
    job = Job("pending", (ACCT_A,))
    with pytest.raises(ValueError, match="no workloads are awaiting review"):
        job.decide("anything", "approve")


def test_decisions_are_recorded_in_the_event_log() -> None:
    runs = _runs()
    job = runs.start([ACCT_A], REGION)
    workload_id = job.result.workloads[0].workload_id  # type: ignore[union-attr]
    job.decide(workload_id, "approve")
    decided = [e for e in job.events_after(0) if e.kind == "decided"]
    assert [e.detail for e in decided] == [f"{workload_id}:approve"]


# --- planners ------------------------------------------------------------------------------

def test_ambient_planner_serves_exactly_one_account() -> None:
    with pytest.raises(ValueError, match="one account"):
        AmbientPlanner().prepare((ACCT_A, ACCT_B), REGION)


def test_ambient_planner_refuses_an_account_the_credential_does_not_own() -> None:
    class Sts:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": ACCT_B}

    planner = AmbientPlanner(sts_factory=lambda _region: Sts())
    with pytest.raises(ValueError, match="different account"):
        planner.prepare((ACCT_A,), REGION)


def test_assumed_role_planner_refuses_an_account_outside_the_run_before_any_call() -> None:
    sts = boto3.client("sts", region_name=REGION, aws_access_key_id="testing-not-a-key",
                       aws_secret_access_key="testing-not-a-secret")
    stubber = Stubber(sts)
    stubber.activate()
    planner = AssumedRolePlanner("example-external-id", sts_factory=lambda _r: sts)
    collect = planner.prepare((ACCT_A, ACCT_B), REGION)
    from workload_graph.auth.session import OutOfScopeAccountError
    with pytest.raises(OutOfScopeAccountError):
        collect(ACCT_C, lambda _kind, _detail: None)
    stubber.assert_no_pending_responses()


def test_assumed_role_planner_needs_an_external_id() -> None:
    with pytest.raises(ValueError, match="external id"):
        AssumedRolePlanner("")


def test_the_assumed_role_planner_names_the_tam_role() -> None:
    assert AssumedRolePlanner("example-external-id").role_name == TAM_ROLE_NAME


# --- runs registry -------------------------------------------------------------------------

def test_a_started_run_can_be_found_by_its_id_and_unknown_ids_are_none() -> None:
    runs = _runs()
    job = runs.start([ACCT_A], REGION)
    assert runs.get(job.job_id) is job
    assert runs.get("missing") is None


def test_start_refuses_bad_input_before_creating_a_job() -> None:
    runs = _runs()
    with pytest.raises(ValueError):
        runs.start(["nope"], REGION)
    with pytest.raises(ValueError):
        runs.start([ACCT_A], "not-a-region")


# --- JSON routes ---------------------------------------------------------------------------

def test_create_job_returns_202_with_the_job_id_and_sorted_accounts() -> None:
    status, body = create_job(_runs(), {"accounts": [ACCT_A], "region": REGION})
    assert status == 202
    assert body["accounts"] == [ACCT_A]
    assert body["job_id"]


@pytest.mark.parametrize("payload", [None, {"region": REGION}, {"accounts": "x", "region": REGION},
                                     {"accounts": ["nope"], "region": REGION}])
def test_create_job_returns_400_for_bad_payloads(payload: Any) -> None:
    status, body = create_job(_runs(), payload)
    assert status == 400
    assert "error" in body


def test_poll_returns_events_after_the_last_seen_number_and_the_result_once_built() -> None:
    runs = _runs()
    _, created = create_job(runs, {"accounts": [ACCT_A], "region": REGION})
    status, body = poll_job(runs, created["job_id"], after=0, wait=0)
    assert status == 200
    assert body["state"] == "awaiting_review"
    assert body["settled"] is True
    kinds = [event["kind"] for event in body["events"]]
    assert kinds[0] == "running" and kinds[-1] == "awaiting_review"
    assert body["result"]["workloads"]
    _, later = poll_job(runs, created["job_id"], after=body["events"][-1]["seq"], wait=0)
    assert later["events"] == []


def test_poll_on_an_unknown_job_is_404() -> None:
    status, body = poll_job(_runs(), "missing", after=0, wait=0)
    assert status == 404
    assert "error" in body


def test_review_route_records_approve_and_reject_and_refuses_unknown_workloads() -> None:
    runs = _runs()
    _, created = create_job(runs, {"accounts": [ACCT_A], "region": REGION})
    _, polled = poll_job(runs, created["job_id"], after=0, wait=0)
    workload_id = polled["result"]["workloads"][0]["workload_id"]
    status, body = review_workload(runs, created["job_id"],
                                   {"workload_id": workload_id, "decision": "reject"})
    assert status == 200
    assert body["decisions"] == {workload_id: "reject"}
    status, body = review_workload(runs, created["job_id"],
                                   {"workload_id": "nope", "decision": "approve"})
    assert status == 400
    status, _ = review_workload(runs, "missing", {"workload_id": "x", "decision": "approve"})
    assert status == 404
