"""Spend change, spend drivers and workload spend trend (Section 10.7).

Status: real
Purpose: say what changed between the last two complete months, per account and in aggregate,
    and which services moved most. Never why (rule 7).
Inputs: cost facts, linked accounts, ``as_of``, the findings ruleset, attribution entries.
Outputs: ``spend_findings`` -> findings and notes.
Planned dependencies: cost, findings.models, findings.data_quality, findings.attribution.
Notes:
    - Only facts with ``estimated == False`` enter a month-over-month comparison. The current
      partial month is reported on its own, labelled estimated, and never compared (F-D8).
    - Drivers are account level only (F-D9). Workload level gets a total trend from the tag method.
    - The aggregate scope is emitted only when more than one account is in scope; with one account
      it would repeat the account's figures.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from workload_graph.cost.models import CostFact
from workload_graph.cost.money import CurrencyMismatchError, safe_ratio, sum_same_unit
from workload_graph.cost.periods import Period, complete_months, current_partial_month
from workload_graph.findings.attribution import EVIDENCE_CAP, WorkloadSpend
from workload_graph.findings.data_quality import dq_finding
from workload_graph.findings.models import (
    RULE_VERSION,
    Confidence,
    Finding,
    FindingKind,
    Value,
)
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import Evidence

ALL = "all"


def _evidence(facts: Sequence[CostFact]) -> tuple[Evidence, ...]:
    unique = {f.evidence.sort_key(): f.evidence for f in facts}
    return tuple(unique[k] for k in sorted(unique))[:EVIDENCE_CAP]


def _ratio(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _scope_findings(scope: str, account_id: str | None, facts: Sequence[CostFact],
                    months: list[Period], partial: Period | None,
                    ruleset: FindingsRuleset) -> list[Finding]:
    findings: list[Finding] = []
    prior_month, current_month = months[-2], months[-1]
    prior = [f for f in facts if f.period == prior_month and not f.estimated]
    current = [f for f in facts if f.period == current_month and not f.estimated]

    if partial is not None:
        partial_facts = [f for f in facts if f.period.start == partial.start]
        try:
            amount, unit = sum_same_unit((f.amount, f.unit) for f in partial_facts)
        except CurrencyMismatchError:
            partial_facts = []
        if partial_facts:
            findings.append(Finding.create(
                kind=FindingKind.SPEND_CHANGE, rule_id="spend_change_partial_month",
                rule_version=RULE_VERSION, workload_id=None, account_id=account_id,
                confidence=Confidence.MEDIUM,
                values={"amount": str(amount), "unit": unit, "estimated": True,
                        "period": partial_facts[0].period.label()},
                evidence=_evidence(partial_facts), subject={"scope": scope},
                flags=("estimated",)))

    if not prior or not current:
        if facts:
            findings.append(dq_finding(
                "insufficient_history", account_id,
                {"scope": scope, "missing": "prior" if not prior else "current",
                 "months": f"{prior_month.label()},{current_month.label()}"},
                _evidence(facts), {"scope": scope}))
        return findings
    try:
        prior_total, unit = sum_same_unit((f.amount, f.unit) for f in prior)
        current_total, current_unit = sum_same_unit((f.amount, f.unit) for f in current)
        if unit != current_unit:
            raise CurrencyMismatchError("months differ")
    except CurrencyMismatchError:
        findings.append(dq_finding("currency_mismatch", account_id, {"scope": scope},
                                   _evidence(prior + current), {"scope": scope, "rule": "spend"}))
        return findings
    if prior_total == 0:
        findings.append(dq_finding(
            "insufficient_history", account_id,
            {"scope": scope, "missing": "prior total is zero",
             "months": f"{prior_month.label()},{current_month.label()}"},
            _evidence(prior + current), {"scope": scope}))
        return findings

    delta = current_total - prior_total
    findings.append(Finding.create(
        kind=FindingKind.SPEND_CHANGE, rule_id="spend_change", rule_version=RULE_VERSION,
        workload_id=None, account_id=account_id, confidence=Confidence.HIGH,
        values={"prior": str(prior_total), "current": str(current_total), "amount": str(delta),
                "pct": _ratio(safe_ratio(delta, prior_total)), "unit": unit,
                "prior_month": prior_month.label(), "current_month": current_month.label()},
        evidence=_evidence(prior + current), subject={"scope": scope}))

    services = sorted({f.dimensions.get("service", "") for f in prior + current})
    moves: list[tuple[Decimal, str, Decimal, Decimal]] = []
    for service in services:
        before = sum((f.amount for f in prior if f.dimensions.get("service") == service),
                     Decimal(0))
        after = sum((f.amount for f in current if f.dimensions.get("service") == service),
                    Decimal(0))
        moves.append((after - before, service, before, after))
    moves.sort(key=lambda m: (-abs(m[0]), m[1]))
    selected = [m for m in moves if abs(m[0]) >= ruleset.driver_min_abs][:ruleset.driver_top_n]
    for change, service, before, after in selected:
        flags: list[str] = []
        if before == 0 and after > 0:
            flags.append("new_service")
        if after == 0 and before > 0:
            flags.append("ended_service")
        values: dict[str, Value] = {
            "service": service, "prior": str(before), "current": str(after),
            "amount": str(change), "pct": _ratio(safe_ratio(change, before) if before else None),
            "contribution_share": _ratio(safe_ratio(change, delta)), "unit": unit,
            "current_month": current_month.label()}
        service_facts = [f for f in prior + current if f.dimensions.get("service") == service]
        findings.append(Finding.create(
            kind=FindingKind.SPEND_DRIVER, rule_id="spend_driver", rule_version=RULE_VERSION,
            workload_id=None, account_id=account_id, confidence=Confidence.HIGH, values=values,
            evidence=_evidence(service_facts), subject={"scope": scope, "service": service},
            flags=tuple(flags)))
    return findings


def _workload_trend(entry: WorkloadSpend) -> Finding | None:
    if entry.method != "tag" or len(entry.series) < 2 or not entry.evidence:
        return None
    prior, current = entry.series[-2], entry.series[-1]
    delta = current.amount - prior.amount
    values: dict[str, Value] = {
        "prior": str(prior.amount), "current": str(current.amount), "amount": str(delta),
        "pct": _ratio(safe_ratio(delta, prior.amount)), "unit": entry.unit, "method": "tag"}
    for month in entry.series:
        values[f"month_{month.period.start.isoformat()[:7]}"] = str(month.amount)
    return Finding.create(
        kind=FindingKind.WORKLOAD_SPEND, rule_id="workload_spend", rule_version=RULE_VERSION,
        workload_id=entry.workload_id, account_id=None, confidence=entry.confidence,
        values=values, evidence=entry.evidence, subject={"workload": entry.workload_id})


def spend_findings(facts: Sequence[CostFact], linked_accounts: Sequence[str], as_of: date,
                   ruleset: FindingsRuleset,
                   spend: Sequence[WorkloadSpend] = ()) -> list[Finding]:
    """Spend change and driver findings per account (and in aggregate), plus workload trends."""
    months = complete_months(as_of, 2)
    partial = current_partial_month(as_of)
    monthly = [f for f in facts if f.kind == "account_service_month"]
    accounts = sorted(set(linked_accounts))
    findings: list[Finding] = []
    for account in accounts:
        scoped = [f for f in monthly if f.dimensions.get("linked_account") == account]
        findings += _scope_findings(account, account, scoped, months, partial, ruleset)
    if len(accounts) > 1:
        scoped = [f for f in monthly if f.dimensions.get("linked_account") in accounts]
        findings += _scope_findings(ALL, None, scoped, months, partial, ruleset)
    for entry in spend:
        trend = _workload_trend(entry)
        if trend is not None:
            findings.append(trend)
    return findings
