"""Spend attribution to workloads (Section 10.6).

Status: real
Purpose: assign spend to workloads by the strongest available method, and reconcile every dollar
    of each account's window total into explicit buckets.
Inputs: cost facts, a ``ResourceIndex``, the findings ruleset, the workload tag keys, ``as_of``.
Outputs: ``AttributionResult`` (workload spend entries, bucket tables, findings, notes, and the
    tag pair each workload resolved to).
Planned dependencies: cost, findings.index, findings.models, findings.data_quality.
Notes:
    - Method ladder (F-D5): tag (monthly) > resource-level (13-day window) > sole-workload account
      > none. Every available method is kept; one is marked primary.
    - Buckets: ``workload:<id>``, ``shared``, ``unplaced``, ``unknown``, ``no_resource_id`` and
      ``unexplained``. Their sum equals the account window total exactly (``Decimal`` equality).
    - No cost is allocated to shared infrastructure (F-D7).
      # DECISION-PENDING: an allocation rule for shared infrastructure is a business decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from workload_graph.cost.models import CostFact
from workload_graph.cost.money import CurrencyMismatchError, safe_ratio, sum_same_unit
from workload_graph.cost.periods import Period, complete_months
from workload_graph.findings.data_quality import dq_finding
from workload_graph.findings.index import ResourceIndex
from workload_graph.findings.models import Confidence, Finding, min_confidence
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import CandidateWorkload, Evidence

Method = Literal["tag", "resource_level", "sole_account", "none"]
METHOD_ORDER: tuple[Method, ...] = ("tag", "resource_level", "sole_account")
NO_RESOURCE_ID = "NoResourceId"  # ASSUMPTION: Cost Explorer's literal (assumption 9).
EVIDENCE_CAP = 20  # SKELETON: provisional cap on evidence per spend entry


class MonthAmount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    period: Period
    amount: Decimal


class WorkloadSpend(BaseModel):
    """One attribution method's figure for one workload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workload_id: str
    method: Method
    primary: bool = False
    amount: Decimal | None
    unit: str | None
    period: Period | None
    confidence: Confidence
    explained_ratio: Decimal | None = None
    series: tuple[MonthAmount, ...] = ()
    notes: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    def daily_average(self) -> Decimal | None:
        """Amount per day of its period, for comparing methods over different windows."""
        if self.amount is None or self.period is None:
            return None
        return self.amount / self.period.days()


class BucketTable(BaseModel):
    """Reconciliation of one account's window total."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    period: Period
    unit: str
    total: Decimal
    rows: dict[str, Decimal]
    explained_ratio: Decimal | None
    evidence: Evidence

    def unattributed(self) -> Decimal:
        """Everything not in a ``workload:`` bucket."""
        return sum((v for k, v in self.rows.items() if not k.startswith("workload:")),
                   Decimal(0))


class AttributionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    spend: tuple[WorkloadSpend, ...] = ()
    buckets: tuple[BucketTable, ...] = ()
    findings: tuple[Finding, ...] = ()
    notes: tuple[str, ...] = ()
    tag_pairs: dict[str, tuple[str, str]] = {}
    declared_tag_keys: dict[str, tuple[str, ...]] = {}


def _cap(items: Iterable[Evidence]) -> tuple[Evidence, ...]:
    unique = {e.sort_key(): e for e in items}
    return tuple(unique[k] for k in sorted(unique))[:EVIDENCE_CAP]


# --- method A ---------------------------------------------------------------------------------

def _bucket_for(index: ResourceIndex, account: str, resource_id: str) -> str:
    if resource_id == NO_RESOURCE_ID:
        return "no_resource_id"
    placement = index.placement(account, resource_id)
    if placement.kind == "workload":
        return f"workload:{placement.workload_id}"
    if placement.kind in ("shared", "unplaced"):
        return placement.kind
    return "unknown"


def _resource_level(facts: Sequence[CostFact], index: ResourceIndex, ruleset: FindingsRuleset,
                    notes: set[str], findings: list[Finding]) -> list[BucketTable]:
    totals = {f.dimensions.get("linked_account", ""): f for f in facts
              if f.kind == "account_window_total"}
    by_account: dict[str, list[CostFact]] = {}
    for fact in facts:
        if fact.kind == "resource_day":
            by_account.setdefault(fact.dimensions.get("linked_account", ""), []).append(fact)
    tables: list[BucketTable] = []
    for account in sorted(by_account):
        total = totals.get(account)
        if total is None:
            notes.add(f"resource-level spend for {account} has no window total; not reconciled")
            continue
        rows: dict[str, Decimal] = {}
        try:
            sum_same_unit([(f.amount, f.unit) for f in by_account[account]]
                          + [(total.amount, total.unit)])
        except CurrencyMismatchError:
            findings.append(dq_finding(
                "currency_mismatch", account, {"scope": "resource_level"},
                [total.evidence], {"scope": "resource_level", "account": account}))
            continue
        for fact in sorted(by_account[account], key=lambda f: f.sort_key()):
            if fact.period.start < total.period.start or fact.period.end > total.period.end:
                notes.add(f"resource-level row outside the window for {account}; not counted")
                continue
            bucket = _bucket_for(index, account, fact.dimensions.get("resource_id", ""))
            rows[bucket] = rows.get(bucket, Decimal(0)) + fact.amount
        explicit = sum(rows.values(), Decimal(0))
        rows["unexplained"] = total.amount - explicit
        if rows["unexplained"] < 0:
            notes.add(f"resource-level rows for {account} exceed its window total")
        explained = safe_ratio(total.amount - rows["unexplained"], total.amount)
        tables.append(BucketTable(
            account_id=account, period=total.period, unit=total.unit, total=total.amount,
            rows={k: rows[k] for k in sorted(rows)}, explained_ratio=explained,
            evidence=total.evidence))
        if explained is None or explained < ruleset.attribution_low_explained_ratio:
            findings.append(dq_finding(
                "attribution_low_explained_ratio", account,
                {"explained_ratio": None if explained is None else str(explained),
                 "threshold": str(ruleset.attribution_low_explained_ratio),
                 "amount": str(rows["unexplained"]), "unit": total.unit},
                [total.evidence], {"account": account, "window": total.period.label()}))
        outside = sum((v for k, v in rows.items() if not k.startswith("workload:")), Decimal(0))
        share = safe_ratio(outside, total.amount)
        if share is not None and share > ruleset.unplaced_share_flag:
            findings.append(dq_finding(
                "unplaced_share_high", account,
                {"share": str(share), "threshold": str(ruleset.unplaced_share_flag),
                 "amount": str(outside), "unit": total.unit},
                [total.evidence], {"account": account, "window": total.period.label()}))
    return tables


# --- method B ---------------------------------------------------------------------------------

def _tag_pair(workload: CandidateWorkload, index: ResourceIndex, tag_keys: Sequence[str],
              ruleset: FindingsRuleset) -> tuple[tuple[str, str] | None, str, list[str]]:
    """``(pair or None, reason, keys found on declared members)``."""
    declared = [m for m in workload.members if m.membership == "declared"]
    if not declared:
        return None, "tag_unavailable", []
    wanted = {k.lower() for k in tag_keys}
    qualifying: list[tuple[str, str]] = []
    found_keys: list[str] = []
    several_values = False
    for key in sorted(wanted):
        values: dict[str, int] = {}
        for member in declared:
            tags = {k.lower(): v for k, v in index.tags_of(member.key).items()}
            if tags.get(key):
                values[tags[key]] = values.get(tags[key], 0) + 1
        if not values:
            continue
        found_keys.append(key)
        if len(values) > 1:
            several_values = True
            continue
        value, count = next(iter(values.items()))
        if Decimal(count) / Decimal(len(declared)) >= ruleset.tag_min_member_share:
            qualifying.append((key, value))
    if len(qualifying) == 1:
        return qualifying[0], "", found_keys
    if len(qualifying) > 1 or several_values:
        return None, "tag_ambiguous", found_keys
    return None, "tag_unavailable", found_keys


def _tag_method(workload: CandidateWorkload, pair: tuple[str, str], facts: Sequence[CostFact],
                months: Sequence[Period], confidence: Confidence) -> WorkloadSpend | None:
    key, value = pair
    matching = [f for f in facts if f.kind == "tag_month"
                and f.dimensions.get("tag_key", "").lower() == key
                and f.dimensions.get("tag_value") == value
                and f.dimensions.get("linked_account") in workload.accounts and not f.estimated]
    keyed = [f for f in facts if f.kind == "tag_month"
             and f.dimensions.get("tag_key", "").lower() == key]
    if not keyed:
        return None
    try:
        _, unit = sum_same_unit((f.amount, f.unit) for f in keyed)
    except CurrencyMismatchError:
        return None
    series = []
    for month in months:
        amount = sum((f.amount for f in matching if f.period == month), Decimal(0))
        series.append(MonthAmount(period=month, amount=amount))
    latest = series[-1]
    return WorkloadSpend(
        workload_id=workload.workload_id, method="tag", amount=latest.amount, unit=unit,
        period=latest.period, confidence=confidence, series=tuple(series),
        notes=(f"tag {key}={value}: includes every resource with this tag, including resources "
               "outside the workload map",),
        evidence=_cap(f.evidence for f in matching) or _cap(f.evidence for f in keyed))


# --- method C ---------------------------------------------------------------------------------

def _sole_account(workload: CandidateWorkload, index: ResourceIndex, facts: Sequence[CostFact],
                  month: Period, ruleset: FindingsRuleset) -> WorkloadSpend | None:
    accounts = sorted({m.key.account_id for m in workload.members})
    if len(accounts) != 1:
        return None
    account = accounts[0]
    for other in index.workloads.values():
        if other.workload_id != workload.workload_id and \
                any(m.key.account_id == account for m in other.members):
            return None
    outside, total = index.account_resource_counts(account)
    share = safe_ratio(Decimal(outside), Decimal(total))
    if share is None or share > ruleset.sole_workload_max_unplaced_ratio:
        return None
    month_facts = [f for f in facts if f.kind == "account_service_month" and not f.estimated
                   and f.period == month and f.dimensions.get("linked_account") == account]
    if not month_facts:
        return None
    try:
        amount, unit = sum_same_unit((f.amount, f.unit) for f in month_facts)
    except CurrencyMismatchError:
        return None
    return WorkloadSpend(
        workload_id=workload.workload_id, method="sole_account", amount=amount, unit=unit,
        period=month, confidence=Confidence.LOW,
        notes=("assumes the whole account belongs to this workload",),
        evidence=_cap(f.evidence for f in month_facts))


# --- orchestration ----------------------------------------------------------------------------

def attribute(facts: Sequence[CostFact], index: ResourceIndex, ruleset: FindingsRuleset,
              workload_tag_keys: Sequence[str], as_of: date) -> AttributionResult:
    """Run every method and pick a primary per workload."""
    notes: set[str] = set(index.notes)
    findings: list[Finding] = []
    months = complete_months(as_of, ruleset.lookback_months)
    method_confidence = {m: Confidence(level) for m, level in ruleset.method_confidence.items()}

    tables = _resource_level(facts, index, ruleset, notes, findings)
    resource_amounts: dict[str, list[tuple[BucketTable, Decimal]]] = {}
    for table in tables:
        for bucket, amount in table.rows.items():
            if bucket.startswith("workload:"):
                resource_amounts.setdefault(bucket.removeprefix("workload:"), []).append(
                    (table, amount))

    spend: list[WorkloadSpend] = []
    tag_pairs: dict[str, tuple[str, str]] = {}
    declared_keys: dict[str, set[str]] = {}
    for workload_id in sorted(index.workloads):
        workload = index.workloads[workload_id]
        base = Confidence(workload.confidence)
        entries: dict[Method, WorkloadSpend] = {}

        pair, reason, found_keys = _tag_pair(workload, index, workload_tag_keys, ruleset)
        for key in found_keys:
            declared_keys.setdefault(key, set()).add(workload_id)
        if pair is None:
            notes.add(f"workload {workload_id}: tag method skipped ({reason})")
        else:
            tag_pairs[workload_id] = pair
            entry = _tag_method(workload, pair, facts, months,
                                min_confidence(base, method_confidence["tag"]))
            if entry is not None:
                entries["tag"] = entry

        if workload_id in resource_amounts:
            rows = resource_amounts[workload_id]
            try:
                amount, unit = sum_same_unit((a, t.unit) for t, a in rows)
                comparable = True
            except CurrencyMismatchError:
                comparable = False
                notes.add(f"workload {workload_id}: resource-level spend mixes currencies")
            if comparable:
                ratios = [t.explained_ratio for t, _ in rows]
                explained = None if any(r is None for r in ratios) else min(
                    r for r in ratios if r is not None)
                confidence = min_confidence(base, method_confidence["resource_level"])
                if explained is None or explained < ruleset.attribution_low_explained_ratio:
                    confidence = Confidence.LOW
                period = rows[0][0].period
                entries["resource_level"] = WorkloadSpend(
                    workload_id=workload_id, method="resource_level", amount=amount, unit=unit,
                    period=period, confidence=confidence, explained_ratio=explained,
                    evidence=_cap(t.evidence for t, _ in rows))

        if not entries:
            sole = _sole_account(workload, index, facts, months[-1], ruleset)
            if sole is not None:
                entries["sole_account"] = sole.model_copy(update={"confidence": min_confidence(
                    min_confidence(base, method_confidence["sole_account"]), Confidence.LOW)})

        if not entries:
            spend.append(WorkloadSpend(
                workload_id=workload_id, method="none", primary=True, amount=None, unit=None,
                period=None, confidence=Confidence.LOW,
                notes=("no attribution method applies to this workload",)))
            continue

        primary = next(m for m in METHOD_ORDER if m in entries)
        for method in METHOD_ORDER:
            if method in entries:
                spend.append(entries[method].model_copy(update={"primary": method == primary}))

        tag_entry, rl_entry = entries.get("tag"), entries.get("resource_level")
        if tag_entry is not None and rl_entry is not None and tag_entry.unit == rl_entry.unit:
            a, b = tag_entry.daily_average(), rl_entry.daily_average()
            if a is not None and b is not None and a != 0 and b != 0:
                difference = safe_ratio(abs(a - b), max(abs(a), abs(b)))
                if difference is not None and difference > ruleset.attribution_disagreement_ratio:
                    findings.append(dq_finding(
                        "attribution_disagreement", workload.accounts[0] if
                        len(workload.accounts) == 1 else None,
                        {"tag_daily_average": str(a), "resource_level_daily_average": str(b),
                         "relative_difference": str(difference),
                         "threshold": str(ruleset.attribution_disagreement_ratio),
                         "unit": tag_entry.unit,
                         "comparison": "daily averages over each method's own window"},
                        tag_entry.evidence + rl_entry.evidence, {"workload": workload_id},
                        workload_id=workload_id))

    return AttributionResult(
        spend=tuple(spend), buckets=tuple(tables),
        findings=tuple(sorted(findings, key=lambda f: f.finding_id)),
        notes=tuple(sorted(notes)), tag_pairs=tag_pairs,
        declared_tag_keys={k: tuple(sorted(v)) for k, v in sorted(declared_keys.items())})
