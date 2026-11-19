"""Cost review after approval: registry, run store layout, routes. Offline; fake collectors."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from wg_cost_builders import scenario
from wg_test_helpers import ACCT_A, ACCT_B, REGION, scenario_snapshots

from workload_graph.cost.snapshots import load_cost_snapshot
from workload_graph.costreview import scope_for
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.jobs import Job
from workload_graph.models import RawSnapshot
from workload_graph.report.models import FindingsReport
from workload_graph.ruleset import Ruleset
from workload_graph.runs import AmbientPlanner, Runs
from workload_graph.runstore import RunStore
from workload_graph.ui_server import get_report, list_runs, poll_job, start_cost_review

MOMENT = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


class _Planner:
    def __init__(self) -> None:
        self.cost_calls: list[tuple[str, ...]] = []

    def prepare(self, accounts: tuple[str, ...], region: str) -> Any:
        snapshots = scenario_snapshots("s1")

        def collect(account_id: str, _progress: Any) -> list[RawSnapshot]:
            return list(snapshots) if account_id == ACCT_A else []
        return collect

    def cost_factory(self, accounts: tuple[str, ...]) -> Any:
        self.cost_calls.append(accounts)
        return lambda *_a: None


def _fake_collect(_factory: Any, account_id: str, _scope: Any, _rules: Any, _at: Any,
                  progress: Any) -> list[RawSnapshot]:
    progress("cost_source_started", "cost_explorer")
    _, cost, _ = scenario(account_id)
    progress("cost_source_done", "cost_explorer:ok")
    return cost


def _runs(tmp_path: Path, collect: Any = _fake_collect, planner: Any = None) -> Runs:
    return Runs(planner or _Planner(), threaded=False, store=RunStore(tmp_path),
                cost_collect=collect, clock=lambda: MOMENT)


def _approved_run(runs: Runs) -> Job:
    job = runs.start([ACCT_A], REGION)
    first = job.result.workloads[0].workload_id  # type: ignore[union-attr]
    runs.decide(job.job_id, first, "approve")
    return job


def test_cost_review_needs_an_approval(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = runs.start([ACCT_A], REGION)
    assert start_cost_review(runs, job.job_id) == (
        400, {"error": "approve at least one workload first"})
    assert start_cost_review(runs, "0" * 32)[0] == 404
    assert job.cost_state == "none"


def test_cost_review_builds_and_saves_the_report_for_approved_workloads(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = _approved_run(runs)
    status, body = start_cost_review(runs, job.job_id)
    assert status == 202
    assert job.cost_state == "done" and job.cost_detail.endswith("findings")
    status, report = get_report(runs, job.job_id)
    assert status == 200
    parsed = FindingsReport.model_validate(report)
    assert {w.workload_id for w in parsed.workloads} == job.approved()
    assert parsed.generated_from.as_of == "2026-11-19"
    kinds = [e.kind for e in job.events_after(0)]
    assert kinds[-4:] == ["cost_started", "cost_source_started", "cost_source_done", "cost_done"]


def test_the_run_folder_keeps_graph_cost_and_report(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = _approved_run(runs)
    runs.cost_review(job.job_id)
    folder = tmp_path / job.job_id
    assert (folder / "graph" / ACCT_A / "tags.json").is_file()
    saved = load_cost_snapshot(folder / "cost" / ACCT_A / "cost_explorer.json")
    _, original, _ = scenario(ACCT_A)
    assert saved == original[0]
    assert (folder / "report.json").read_text().endswith("}\n")


def test_tags_from_the_saved_graph_snapshots_reach_the_report(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = _approved_run(runs)
    runs.cost_review(job.job_id)
    report = FindingsReport.model_validate_json((tmp_path / job.job_id / "report.json")
                                                .read_text())
    assert "graph snapshots not given: tag attribution unavailable" not in report.notes


def test_one_failing_account_is_isolated_with_a_code(tmp_path: Path) -> None:
    def collect(factory: Any, account_id: str, *rest: Any) -> list[RawSnapshot]:
        if account_id == ACCT_B:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                              "GetCostAndUsage")
        return _fake_collect(factory, account_id, *rest)

    runs = _runs(tmp_path, collect=collect)
    job = runs.start([ACCT_A, ACCT_B], REGION)
    runs.decide(job.job_id, job.result.workloads[0].workload_id, "approve")  # type: ignore[union-attr]
    runs.cost_review(job.job_id)
    failed = [e for e in job.events_after(0) if e.kind == "cost_account_failed"]
    assert [(e.account_id, e.detail) for e in failed] == [(ACCT_B, "AccessDeniedException")]
    assert job.cost_state == "done"
    report = FindingsReport.model_validate_json(runs.report(job.job_id) or "")
    assert f"cost data for {ACCT_B} was not collected: AccessDeniedException" in report.notes


def test_a_crash_ends_the_review_failed_with_a_code(tmp_path: Path) -> None:
    def broken(*_a: Any) -> list[RawSnapshot]:
        return [RawSnapshot.model_construct(source="cost_explorer")]  # malformed on purpose

    runs = _runs(tmp_path, collect=broken)
    job = _approved_run(runs)
    runs.cost_review(job.job_id)
    assert job.cost_state == "failed" and job.cost_detail
    assert get_report(runs, job.job_id)[0] == 404


def test_decisions_are_frozen_while_the_review_runs_and_polling_waits(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = _approved_run(runs)
    job.begin_cost()
    with pytest.raises(ValueError, match="cost review is running"):
        job.decide(job.result.workloads[0].workload_id, "reject")  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="already running"):
        job.begin_cost()
    status, body = poll_job(runs, job.job_id, 0, 0)
    assert status == 200 and body["settled"] is False
    assert body["cost"] == {"state": "running", "detail": ""}
    job.finish_cost("done", "0 findings")
    assert poll_job(runs, job.job_id, 0, 0)[1]["settled"] is True


def test_a_review_saved_mid_way_restores_as_failed_interrupted(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = _approved_run(runs)
    job.begin_cost()
    RunStore(tmp_path).save(job)
    restored = {j.job_id: j for j in RunStore(tmp_path).load_all()}[job.job_id]
    assert (restored.cost_state, restored.cost_detail) == ("failed", "interrupted")


def test_run_list_shows_the_cost_state(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    job = _approved_run(runs)
    runs.cost_review(job.job_id)
    (summary,) = list_runs(runs)[1]["runs"]
    assert summary["cost_state"] == "done" and summary["approved"] == 1


def test_ambient_cost_factory_check_the_account_first() -> None:
    class Sts:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": ACCT_B}

    with pytest.raises(ValueError, match="different account"):
        AmbientPlanner(sts_factory=lambda _r: Sts()).cost_factory((ACCT_A,))


def test_scope_uses_the_workload_tag_keys() -> None:
    scope = scope_for(FindingsRuleset(), Ruleset(), MOMENT.date())
    assert scope.tag_keys == tuple(Ruleset().workload_tag_keys)
    assert (scope.max_calls, scope.resource_level_window_days) == (40, 13)
