"""Cost review after approval: collect cost data for a reviewed run and build its report.

Status: real
Purpose: the live half of the cost layer. For each account of a run, collect Cost Explorer and
    Cost Optimization Hub through the run's credentials, save the snapshots, then build the
    findings report over the approved workloads only.
Inputs: a settled ``Job`` with approvals, a client factory (us-east-1), a ``RunStore``, as_of.
Outputs: ``run_cost_review``, ``collect_cost``, ``scope_for``.
Planned dependencies: collectors.cost_*, pipeline_findings, runstore, jobs.
Notes:
    - Per-account mode: each account is its own access account. Payer mode (one management
      account reading many linked accounts) is supported by the collectors but not offered here.
    - One account failing is noted as an event with a code and the others still count.
    - Cost Explorer charges per paginated request; ``FindingsRuleset.max_calls`` caps each
      account and collector.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from workload_graph.collectors.cost_explorer import CostExplorerCollector
from workload_graph.collectors.cost_optimization_hub import CostOptimizationHubCollector
from workload_graph.cost.models import AccessPlan, CostScope
from workload_graph.credentials import ClientFactory
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.jobs import Job
from workload_graph.live import Progress
from workload_graph.models import RawSnapshot
from workload_graph.pipeline import load_snapshots
from workload_graph.pipeline_findings import run as build_report
from workload_graph.report.models import FindingsReport
from workload_graph.ruleset import Ruleset
from workload_graph.runstore import RunStore

CostCollect = Callable[[ClientFactory, str, CostScope, FindingsRuleset, datetime, Progress],
                       list[RawSnapshot]]


def scope_for(rules: FindingsRuleset, workload_rules: Ruleset, as_of: date) -> CostScope:
    """What one account's cost collection may request."""
    return CostScope(as_of=as_of, lookback_months=rules.lookback_months, metric=rules.metric,
                     max_calls=rules.max_calls,
                     resource_level_window_days=rules.resource_level_window_days,
                     resource_level_top_services=rules.resource_level_top_services,
                     tag_keys=tuple(workload_rules.workload_tag_keys))


def collect_cost(factory: ClientFactory, account_id: str, scope: CostScope,
                 rules: FindingsRuleset, collected_at: datetime,
                 progress: Progress) -> list[RawSnapshot]:
    """Both cost sources for one account, in per-account mode."""
    plan = AccessPlan(access_account_id=account_id, linked_account_ids=(account_id,))
    snapshots: list[RawSnapshot] = []
    explorer = CostExplorerCollector()
    hub = CostOptimizationHubCollector()
    for source, collect in (("cost_explorer", explorer.collect),
                            ("cost_optimization_hub", hub.collect)):
        progress("cost_source_started", source)
        snapshot = collect(factory, plan, scope, rules, collected_at)
        progress("cost_source_done", f"{source}:{snapshot.status}")
        snapshots.append(snapshot)
    return snapshots


def _code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", type(exc).__name__))
    return type(exc).__name__


def run_cost_review(job: Job, factory: ClientFactory, store: RunStore | None, *,
                    as_of: date, collected_at: datetime,
                    findings_ruleset: FindingsRuleset | None = None,
                    workload_ruleset: Ruleset | None = None,
                    collect: CostCollect = collect_cost) -> FindingsReport | None:
    """Collect, save and report. The job must already be in ``begin_cost``; this ends it."""
    rules = findings_ruleset or FindingsRuleset()
    workload_rules = workload_ruleset or Ruleset()
    try:
        result = job.result
        if result is None:
            raise ValueError("no build result")
        scope = scope_for(rules, workload_rules, as_of)
        snapshots: list[RawSnapshot] = []
        notes: list[str] = []
        for account_id in job.accounts:
            def progress(kind: str, detail: str, account: str = account_id) -> None:
                job.emit(kind, account, detail)

            try:
                collected = collect(factory, account_id, scope, rules, collected_at, progress)
            except Exception as exc:  # isolation: one account must not stop the others
                job.emit("cost_account_failed", account_id, _code(exc))
                notes.append(f"cost data for {account_id} was not collected: {_code(exc)}")
                continue
            snapshots.extend(collected)
        graph: list[RawSnapshot] | None = None
        if store is not None:
            store.save_snapshots(job.job_id, "cost", snapshots)
            graph_dir = store.snapshot_dir(job.job_id, "graph")
            if graph_dir.is_dir():
                graph, graph_notes = load_snapshots(graph_dir)
                notes += graph_notes
        report = build_report(result, snapshots, as_of=as_of, findings_ruleset=rules,
                              workload_ruleset=workload_rules, graph_snapshots=graph,
                              approved=job.approved(), notes=notes)
        if store is not None:
            store.save_report(job.job_id, report.to_json())
    except Exception as exc:  # the review ends failed with a code, never half-done
        job.finish_cost("failed", _code(exc))
        return None
    job.finish_cost("done", f"{len(report.findings)} findings")
    return report
