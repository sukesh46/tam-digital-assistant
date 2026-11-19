"""Report assembly (Section 10.12, second half).

Status: real
Purpose: turn a ``FindingsBundle`` into a ``FindingsReport``: summary, workloads, buckets,
    coverage, evidence index, then the narrative with the guardrail and per-section fallback.
Inputs: ``FindingsBundle``, ``BuildResult``, cost snapshots, ``FindingsRuleset``, a narrator.
Outputs: ``assemble``.
Planned dependencies: findings, report.
Notes:
    - A narrator that raises, or a section that fails the guardrail, is replaced by the template
      narrator's section and the replacement is noted. The structured report never depends on
      the narrative.
    - Summary figures are sums of findings only; recommendations are never added together (the
      savings figure is Cost Optimization Hub's own deduplicated total).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from workload_graph.cost.money import safe_ratio
from workload_graph.cost.periods import complete_months
from workload_graph.findings.build import FindingsBundle
from workload_graph.findings.models import Confidence, Finding, FindingKind
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.models import RawSnapshot
from workload_graph.models.workload import BuildResult
from workload_graph.normalisers.cost_explorer import label_statuses
from workload_graph.report.guardrail import check_narrative
from workload_graph.report.models import (
    SECTION_NAMES,
    Coverage,
    CoverageEntry,
    CoverageReport,
    EvidenceRef,
    FindingsReport,
    GeneratedFrom,
    Narrative,
    NarrativeSection,
    ReportScope,
    SummaryFigure,
    WorkloadSection,
)
from workload_graph.report.narrative import Narrator, TemplateNarrator

COST_SOURCES = ("cost_explorer", "cost_optimization_hub")


def _coverage_status(statuses: dict[str, str]) -> Coverage:
    values = set(statuses.values())
    if not values:
        return "error"
    if values <= {"ok", "empty", "skipped"}:
        return "available" if "ok" in values else "empty"
    if "ok" in values or "empty" in values or "partial" in values:
        return "partial"
    for status in ("not_enabled", "denied"):
        if values <= {status, "skipped"}:
            return status
    return "error"


def coverage(snapshots: Sequence[RawSnapshot], access_accounts: Sequence[str],
             bundle: FindingsBundle) -> tuple[CoverageReport, list[str]]:
    """Per access account and cost source: what was available. Missing snapshots are errors."""
    present: dict[tuple[str, str], RawSnapshot] = {(s.account_id, s.source): s for s in snapshots}
    entries: list[CoverageEntry] = []
    notes: list[str] = []
    for account in sorted(set(access_accounts)):
        for source in COST_SOURCES:
            snapshot = present.get((account, source))
            if snapshot is None:
                entries.append(CoverageEntry(account_id=account, source=source, status="error"))
                notes.append(f"{source} snapshot missing for {account}")
                continue
            statuses = label_statuses(snapshot)
            entries.append(CoverageEntry(account_id=account, source=source,
                                         status=_coverage_status(statuses), labels=statuses))
    kinds = {f.kind for f in bundle.facts}
    return CoverageReport(entries=tuple(entries), tag_cost_visible="tag_month" in kinds,
                          resource_level_visible="resource_day" in kinds), notes


def _figure(name: str, value: Decimal | None, unit: str | None, ids: Sequence[str],
            note: str = "") -> SummaryFigure:
    return SummaryFigure(name=name, value=None if value is None else str(value), unit=unit,
                         finding_ids=tuple(ids), note=note)


def _summary(bundle: FindingsBundle) -> tuple[SummaryFigure, ...]:
    changes = [f for f in bundle.findings if f.rule_id == "spend_change"]
    headline = next((f for f in changes if f.account_id is None), None) or \
        (changes[0] if changes else None)
    figures: list[SummaryFigure] = []
    if headline is None:
        missing = "no comparable complete months"
        figures += [_figure(n, None, None, (), missing) for n in
                    ("latest_month_total", "prior_month_total", "month_over_month_change",
                     "month_over_month_pct")]
    else:
        ids = (headline.finding_id,)
        unit = headline.unit()

        def raw(key: str) -> Decimal | None:
            value = headline.values.get(key)
            return None if value is None else Decimal(str(value))

        figures += [_figure("latest_month_total", raw("current"), unit, ids),
                    _figure("prior_month_total", raw("prior"), unit, ids),
                    _figure("month_over_month_change", raw("amount"), unit, ids),
                    _figure("month_over_month_pct", raw("pct"), None, ids)]

    deduped = bundle.savings.account_deduped
    savings_ids = [f.finding_id for f in bundle.findings
                   if f.kind in (FindingKind.SAVINGS_OPPORTUNITY,
                                 FindingKind.COMMITMENT_OPPORTUNITY)]
    currencies = {s.currency for s in deduped.values()}
    if deduped and len(currencies) == 1:
        total = sum((s.deduped_total for s in deduped.values()), Decimal(0))
        figures.append(_figure("deduplicated_savings_total", total, currencies.pop(), savings_ids,
                               "Cost Optimization Hub's deduplicated total; recommendations "
                               "are not added"))
    else:
        figures.append(_figure("deduplicated_savings_total", None, None, savings_ids,
                               "no single-currency deduplicated total available"))

    quick = [f for f in bundle.findings if f.tier == "quick_win" and f.unit() == bundle.currency]
    quick_sum = sum((f.amount() or Decimal(0) for f in quick), Decimal(0))
    figures.append(_figure("quick_win_sum", quick_sum if quick else None, bundle.currency,
                           [f.finding_id for f in quick],
                           "sum of individual recommendations; they may overlap"))

    buckets = [b for b in bundle.attribution.buckets if b.unit == bundle.currency]
    attribution_ids = [f.finding_id for f in bundle.findings if f.rule_id in
                       ("attribution_low_explained_ratio", "unplaced_share_high")]
    share = safe_ratio(sum((b.unattributed() for b in buckets), Decimal(0)),
                       sum((b.total for b in buckets), Decimal(0))) if buckets else None
    figures.append(_figure("unattributed_share", share, None, attribution_ids,
                           "resource-level window only" if buckets else
                           "resource-level data not available"))
    return tuple(figures)


def _workloads(bundle: FindingsBundle, result: BuildResult,
               approved: set[str] | None) -> tuple[WorkloadSection, ...]:
    sections: list[WorkloadSection] = []
    for workload in sorted(result.workloads, key=lambda w: w.workload_id):
        if approved is not None and workload.workload_id not in approved:
            continue
        spend = sorted((s for s in bundle.attribution.spend
                        if s.workload_id == workload.workload_id),
                       key=lambda s: (not s.primary, s.method))
        total = bundle.savings.workload_totals.get(workload.workload_id)
        sections.append(WorkloadSection(
            workload_id=workload.workload_id, name_hint=workload.name_hint,
            confidence=Confidence(workload.confidence),
            accounts=tuple(sorted(workload.accounts)),
            spend=tuple(spend), savings_sum_of_resource_recommendations=None if total is None
            else str(total),
            findings=tuple(f.finding_id for f in bundle.findings
                           if f.workload_id == workload.workload_id)))
    return tuple(sections)


def _evidence_index(findings: Sequence[Finding]) -> dict[str, tuple[EvidenceRef, ...]]:
    index: dict[str, tuple[EvidenceRef, ...]] = {}
    for finding in findings:
        refs = []
        for ev in finding.evidence:
            snapshot_id, _, pointer = ev.raw_ref.partition("#")
            refs.append(EvidenceRef(source=ev.source, snapshot_id=snapshot_id, pointer=pointer,
                                    detail=ev.detail, observed_at=ev.observed_at.isoformat()))
        index[finding.finding_id] = tuple(refs)
    return index


def _narrate(core: FindingsReport, narrator: Narrator) -> tuple[Narrative, list[str]]:
    notes: list[str] = []
    template = TemplateNarrator().narrate(core)
    try:
        proposed = narrator.narrate(core)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"narrator failed ({type(exc).__name__}); template narrative used")
        proposed = template
    checked = check_narrative(proposed, core)
    template_checked = check_narrative(template, core)
    sections: list[NarrativeSection] = []
    for name in SECTION_NAMES:
        codes = checked.codes_for(name)
        if not codes:
            sections.append(proposed.section(name))
            continue
        if template_checked.codes_for(name):
            notes.append(f"narrative section {name} dropped: template also failed")
            sections.append(NarrativeSection(name=name))
        else:
            notes.append(f"narrative section {name} replaced by template: {','.join(codes)}")
            sections.append(template.section(name))
    return Narrative(sections=tuple(sections)), notes


def _canonical(value: Any) -> Any:  # Any: parsed JSON
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def build_result_sha256(result: BuildResult) -> str:
    """Hash of the build result with every list sorted: list order is not content."""
    canonical = _canonical(json.loads(result.to_json()))
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def assemble(bundle: FindingsBundle, result: BuildResult, *, ruleset: FindingsRuleset,
             snapshots: Sequence[RawSnapshot], access_accounts: Sequence[str],
             linked_accounts: Sequence[str], as_of: date, narrator: Narrator | None = None,
             approved: set[str] | None = None, notes: Sequence[str] = ()) -> FindingsReport:
    """The full report, narrative included."""
    covered, coverage_notes = coverage(snapshots, access_accounts, bundle)
    months = complete_months(as_of, ruleset.lookback_months)
    core = FindingsReport(
        generated_from=GeneratedFrom(
            build_result_sha256=build_result_sha256(result),
            workload_ruleset_hash=result.ruleset_hash,
            findings_ruleset_hash=ruleset.content_hash(),
            snapshot_ids=tuple(sorted(s.snapshot_id for s in snapshots)),
            as_of=as_of.isoformat()),
        scope=ReportScope(accounts=tuple(sorted(set(linked_accounts))),
                          months=tuple(m.label() for m in months), currency=bundle.currency,
                          metric=ruleset.metric),
        coverage=covered,
        summary=_summary(bundle),
        account_level=tuple(f.finding_id for f in bundle.findings if f.workload_id is None),
        workloads=_workloads(bundle, result, approved),
        buckets=bundle.attribution.buckets,
        customer_asks=bundle.asks,
        talking_points=bundle.talking_points,
        findings=bundle.findings,
        evidence_index=_evidence_index(bundle.findings),
        notes=tuple(sorted(set(bundle.notes) | set(notes) | set(coverage_notes))))
    narrative, narrative_notes = _narrate(core, narrator or TemplateNarrator())
    return core.model_copy(update={
        "narrative": narrative,
        "notes": tuple(sorted(set(core.notes) | set(narrative_notes)))})
