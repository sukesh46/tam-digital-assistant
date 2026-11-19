"""Savings opportunities from Cost Optimization Hub (Section 10.8).

Status: real
Purpose: attach each recommendation to a workload when its resource is a member, tier it by
    effort, and compare the item sum with the hub's deduplicated total.
Inputs: recommendations, deduplicated summaries, a ``ResourceIndex``, the findings ruleset,
    ``as_of``, the Cost Explorer unit, and the primary spend entry per workload.
Outputs: ``SavingsResult``.
Planned dependencies: findings.index, findings.models, findings.data_quality.
Notes:
    - Commitment purchases are account level and never in a workload total (F-D10).
    - The authoritative account total is the hub's deduplicated figure (F-D11). Item sums are
      labelled ``sum_of_resource_recommendations`` and are not deduplicated.
    - Quick win needs low effort and ``restart_needed is False``; unknown restart is plan first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from workload_graph.cost.money import safe_ratio
from workload_graph.findings.attribution import WorkloadSpend
from workload_graph.findings.data_quality import dq_finding
from workload_graph.findings.index import Placement, ResourceIndex
from workload_graph.findings.models import (
    RULE_VERSION,
    Confidence,
    Finding,
    FindingKind,
    Tier,
    Value,
    min_confidence,
)
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import Evidence
from workload_graph.normalisers.cost_optimization_hub import DedupedSummary, Recommendation

MONTHLY_METHODS = ("tag", "sole_account")
_FLAG_FOR = {"shared": "shared_infrastructure", "unplaced": "resource_not_in_workload",
             "unknown": "resource_not_in_graph", "ambiguous": "resource_id_ambiguous"}


class SavingsResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[Finding, ...] = ()
    workload_totals: dict[str, Decimal] = {}
    account_deduped: dict[str, DedupedSummary] = {}
    stale: dict[str, tuple[Evidence, ...]] = {}
    notes: tuple[str, ...] = ()


def _placement(index: ResourceIndex, rec: Recommendation) -> Placement:
    target = rec.resource_arn or rec.resource_id
    return Placement("unknown") if target is None else index.placement(rec.account_id, target)


def _tier(rec: Recommendation, ruleset: FindingsRuleset) -> Tier:
    quick = rec.implementation_effort in ruleset.quick_win_efforts
    return "quick_win" if quick and rec.restart_needed is False else "plan_first"


def savings_findings(recommendations: Sequence[Recommendation],
                     summaries: Sequence[DedupedSummary], index: ResourceIndex,
                     ruleset: FindingsRuleset, as_of: date, ce_unit: str | None,
                     primary: Mapping[str, WorkloadSpend]) -> SavingsResult:
    """Findings for every recommendation, workload totals, overlap and stale checks."""
    findings: list[Finding] = []
    notes: set[str] = set()
    workload_totals: dict[str, Decimal] = {}
    stale: dict[str, list[Evidence]] = {}
    mismatched: dict[str, list[Evidence]] = {}
    cutoff = as_of - timedelta(days=ruleset.stale_recommendation_days)

    for rec in sorted(recommendations, key=lambda r: (r.account_id, r.recommendation_id)):
        flags: list[str] = []
        if rec.rollback_possible is False:
            flags.append("no_rollback")
        if rec.last_refresh is not None and rec.last_refresh.date() < cutoff:
            flags.append("stale")
            stale.setdefault(rec.account_id, []).append(rec.evidence)
        currency_ok = ce_unit is None or rec.currency == ce_unit
        if not currency_ok:
            flags.append("currency_mismatch")
            mismatched.setdefault(rec.account_id, []).append(rec.evidence)
        values: dict[str, Value] = {
            "amount": str(rec.estimated_monthly_savings), "unit": rec.currency,
            "action_type": rec.action_type, "recommendation_id": rec.recommendation_id,
            "resource": rec.resource_arn or rec.resource_id,
            "current_resource_type": rec.current_resource_type,
            "recommended_resource_type": rec.recommended_resource_type,
            "implementation_effort": rec.implementation_effort,
            "restart_needed": rec.restart_needed, "rollback_possible": rec.rollback_possible,
            "estimated_monthly_cost": None if rec.estimated_monthly_cost is None
            else str(rec.estimated_monthly_cost),
            "last_refresh": None if rec.last_refresh is None else rec.last_refresh.isoformat(),
        }
        subject = {"recommendation": rec.recommendation_id}

        if rec.action_type in ruleset.commitment_action_types:
            findings.append(Finding.create(
                kind=FindingKind.COMMITMENT_OPPORTUNITY, rule_id="commitment_opportunity",
                rule_version=RULE_VERSION, workload_id=None, account_id=rec.account_id,
                confidence=Confidence.HIGH, values=values, evidence=(rec.evidence,),
                subject=subject, flags=tuple(flags)))
            continue

        placement = _placement(index, rec)
        workload_id: str | None = None
        confidence = Confidence.HIGH
        if placement.kind == "workload" and placement.workload_id is not None:
            workload_id = placement.workload_id
            base = Confidence(index.workloads[workload_id].confidence)
            confidence = base if placement.membership == "declared" else \
                min_confidence(base, Confidence.MEDIUM)
            workload_totals[workload_id] = workload_totals.get(workload_id, Decimal(0)) + \
                rec.estimated_monthly_savings
            entry = primary.get(workload_id)
            if (currency_ok and entry is not None and entry.method in MONTHLY_METHODS
                    and entry.amount and entry.unit == rec.currency):
                ratio = safe_ratio(rec.estimated_monthly_savings, entry.amount)
                values["pct_of_workload_spend"] = None if ratio is None else str(ratio)
        else:
            flags.append(_FLAG_FOR[placement.kind])
        findings.append(Finding.create(
            kind=FindingKind.SAVINGS_OPPORTUNITY, rule_id="savings_opportunity",
            rule_version=RULE_VERSION, workload_id=workload_id, account_id=rec.account_id,
            confidence=confidence, values=values, evidence=(rec.evidence,), subject=subject,
            tier=_tier(rec, ruleset), flags=tuple(flags)))

    deduped = {s.account_id: s for s in summaries}
    accounts = sorted({r.account_id for r in recommendations})
    for account in accounts:
        items = [r for r in recommendations if r.account_id == account]
        summary = deduped.get(account)
        if summary is None:
            notes.add(f"no deduplicated savings total for {account}; no authoritative total")
            continue
        same = [r for r in items if r.currency == summary.currency]
        item_sum = sum((r.estimated_monthly_savings for r in same), Decimal(0))
        if item_sum - summary.deduped_total > ruleset.savings_dedupe_tolerance:
            findings.append(dq_finding(
                "savings_overlap", account,
                {"sum_of_items": str(item_sum), "deduplicated_total": str(summary.deduped_total),
                 "amount": str(item_sum - summary.deduped_total), "unit": summary.currency,
                 "rule": "do not add these figures"},
                [summary.evidence, *(r.evidence for r in same)], {"account": account}))
    for account in sorted(mismatched):
        findings.append(dq_finding(
            "currency_mismatch", account,
            {"scope": "recommendations", "count": len(mismatched[account]),
             "expected_unit": ce_unit or ""},
            mismatched[account], {"scope": "recommendations", "account": account}))

    return SavingsResult(
        findings=tuple(findings),
        workload_totals={k: workload_totals[k] for k in sorted(workload_totals)},
        account_deduped={a: deduped[a] for a in sorted(deduped)},
        stale={a: tuple(stale[a]) for a in sorted(stale)},
        notes=tuple(sorted(notes)))
