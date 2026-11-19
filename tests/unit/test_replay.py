"""Offline replay planner: no network, uses a tiny fixture tree."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from wg_cost_builders import scenario
from wg_test_helpers import ACCT_A, REGION, scenario_snapshots

from workload_graph.cost.snapshots import dump_cost_snapshot
from workload_graph.replay import ReplayPlanner, replay_cost_collect
from workload_graph.runs import Runs
from workload_graph.runstore import RunStore

MOMENT = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


def test_replay_builds_and_cost_reviews_without_aws(tmp_path: Path) -> None:
    run_dir = tmp_path / "saved"
    for snapshot in scenario_snapshots("s1"):
        path = run_dir / "graph" / snapshot.account_id / f"{snapshot.source}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _, cost, _ = scenario(ACCT_A)
    for snapshot in cost:
        path = run_dir / "cost" / snapshot.account_id / f"{snapshot.source}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_cost_snapshot(snapshot), encoding="utf-8")

    runs = Runs(ReplayPlanner(run_dir), threaded=False, store=RunStore(tmp_path / "out"),
                cost_collect=replay_cost_collect(run_dir), clock=lambda: MOMENT)
    job = runs.start([ACCT_A], REGION)
    assert job.state.value == "awaiting_review"
    assert job.result is not None and job.result.workloads
    runs.decide(job.job_id, job.result.workloads[0].workload_id, "approve")
    runs.cost_review(job.job_id)
    assert job.cost_state == "done"
    assert runs.report(job.job_id) is not None
