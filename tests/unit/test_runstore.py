"""Run persistence under ``out/runs/``: runs and decisions survive a restart. All offline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from wg_test_helpers import ACCT_A, REGION, scenario_snapshots

from workload_graph.jobs import Job, JobState
from workload_graph.models import RawSnapshot
from workload_graph.runs import Runs
from workload_graph.runstore import RunStore
from workload_graph.ui_server import list_runs, review_workload


def _planner() -> Any:
    snapshots = scenario_snapshots("s1")

    class Fixture:
        def prepare(self, accounts: tuple[str, ...], region: str) -> Any:
            def collect(account_id: str, _progress: Any) -> list[RawSnapshot]:
                return list(snapshots) if account_id == ACCT_A else []
            return collect

    return Fixture()


def _runs(root: Path) -> Runs:
    return Runs(_planner(), threaded=False, store=RunStore(root))


def test_a_settled_run_is_written_to_its_own_directory(tmp_path: Path) -> None:
    job = _runs(tmp_path).start([ACCT_A], REGION)
    stored = json.loads((tmp_path / job.job_id / "run.json").read_text())
    assert stored["job_id"] == job.job_id
    assert stored["state"] == "awaiting_review"
    assert stored["region"] == REGION
    assert stored["accounts"] == [ACCT_A]
    assert stored["result"]["workloads"]


def test_a_new_registry_finds_runs_and_decisions_from_disk(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = runs.start([ACCT_A], REGION)
    workload_id = job.result.workloads[0].workload_id  # type: ignore[union-attr]
    runs.decide(job.job_id, workload_id, "approve")

    restored = _runs(tmp_path).get(job.job_id)
    assert restored is not None
    assert restored.state is JobState.AWAITING_REVIEW
    assert restored.decisions == {workload_id: "approve"}
    assert restored.result == job.result
    assert restored.region == REGION
    assert [e.kind for e in restored.events_after(0)] == [e.kind for e in job.events_after(0)]


def test_decisions_on_a_restored_run_are_still_accepted_and_saved(tmp_path: Path) -> None:
    job = _runs(tmp_path).start([ACCT_A], REGION)
    workload_id = job.result.workloads[0].workload_id  # type: ignore[union-attr]
    second = _runs(tmp_path)
    second.decide(job.job_id, workload_id, "reject")
    assert _runs(tmp_path).get(job.job_id).decisions == {workload_id: "reject"}  # type: ignore[union-attr]


def test_a_run_interrupted_mid_collection_is_restored_as_failed(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    job = Job("a" * 32, (ACCT_A,), region=REGION)
    job.transition(JobState.RUNNING, "running", "1 accounts")
    store.save(job)
    restored = Runs(_planner(), threaded=False, store=RunStore(tmp_path)).get("a" * 32)
    assert restored is not None
    assert restored.state is JobState.FAILED
    assert restored.events_after(0)[-1].detail == "interrupted"


def test_an_unreadable_run_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / ("b" * 32)).mkdir()
    (tmp_path / ("b" * 32) / "run.json").write_text("{not json")
    (tmp_path / "not-a-run").mkdir()
    runs = _runs(tmp_path)
    assert runs.get("b" * 32) is None
    assert runs.list() == []


def test_the_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    job = _runs(tmp_path).start([ACCT_A], REGION)
    folder = tmp_path / job.job_id
    assert sorted(p.name for p in folder.iterdir()) == ["graph", "run.json"]
    assert not [p for p in folder.rglob("*") if p.name.startswith(".tmp-")]


def test_runs_are_listed_newest_first_with_a_summary(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    first = runs.start([ACCT_A], REGION)
    second = runs.start([ACCT_A], REGION)
    listed = runs.list()
    assert [r["job_id"] for r in listed] == [second.job_id, first.job_id]
    assert listed[0]["state"] == "awaiting_review"
    assert listed[0]["workloads"] == len(second.result.workloads)  # type: ignore[union-attr]
    assert listed[0]["approved"] == 0


def test_list_and_review_routes_use_the_store(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = runs.start([ACCT_A], REGION)
    workload_id = job.result.workloads[0].workload_id  # type: ignore[union-attr]
    status, _ = review_workload(runs, job.job_id, {"workload_id": workload_id,
                                                   "decision": "approve"})
    assert status == 200
    status, body = list_runs(runs)
    assert status == 200
    assert body["runs"][0]["approved"] == 1


def test_a_registry_without_a_store_still_works_in_memory() -> None:
    runs = Runs(_planner(), threaded=False)
    job = runs.start([ACCT_A], REGION)
    assert runs.get(job.job_id) is job
    assert [r["job_id"] for r in runs.list()] == [job.job_id]


def test_store_refuses_a_path_that_is_not_a_run_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RunStore(tmp_path).run_dir("../escape")
