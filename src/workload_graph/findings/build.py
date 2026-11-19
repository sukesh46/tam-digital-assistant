"""Findings orchestration (Section 10.12, first half).

Status: real
Purpose: normalise cost snapshots, run every rule, and order the findings.
Inputs: cost snapshots, a ``ResourceIndex``, a ``FindingsRuleset``, workload tag keys, ``as_of``.
Outputs: ``FindingsBundle``, ``build_findings``, ``order_findings``.
Planned dependencies: normalisers.cost_*, findings.*.
Notes:
    - Section order is fixed: spend change, drivers, workload spend, quick wins, plan-first
      savings, commitments, data quality. Within a section: largest absolute amount first, then
      higher confidence, then finding id. Display ids ``F-001``... follow that order.
    - Pure; no I/O and no wall-clock calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict

from workload_graph.cost.labels import TAG_KEYS_SEEN
from workload_graph.cost.models import CostFact
from workload_graph.findings.asks import Ask, TalkingPoint, build_asks, build_talking_points
from workload_graph.findings.attribution import AttributionResult, attribute
from workload_graph.findings.data_quality import (
    label_evidence,
    source_findings,
    stale_findings,
    tag_visibility_findings,
)
from workload_graph.findings.drivers import spend_findings
from workload_graph.findings.index import ResourceIndex
from workload_graph.findings.models import Finding, FindingKind
from workload_graph.findings.ruleset import FindingsRuleset
from workload_graph.findings.savings import SavingsResult, savings_findings
from workload_graph.models import Evidence, RawSnapshot
from workload_graph.normalisers.cost_explorer import normalise_cost_explorer
from workload_graph.normalisers.cost_optimization_hub import (
    DedupedSummary,
    Recommendation,
    normalise_cost_optimization_hub,
)

_SECTIONS = {FindingKind.SPEND_CHANGE: 0, FindingKind.SPEND_DRIVER: 1,
             FindingKind.WORKLOAD_SPEND: 2, FindingKind.SAVINGS_OPPORTUNITY: 3,
             FindingKind.COMMITMENT_OPPORTUNITY: 5, FindingKind.DATA_QUALITY: 6}


class FindingsBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[Finding, ...]
    attribution: AttributionResult
    savings: SavingsResult
    asks: tuple[Ask, ...]
    talking_points: tuple[TalkingPoint, ...]
    facts: tuple[CostFact, ...]
    currency: str | None
    tag_keys_seen: tuple[str, ...]
    notes: tuple[str, ...]


def _section(finding: Finding) -> int:
    base = _SECTIONS[finding.kind]
    if finding.kind is FindingKind.SAVINGS_OPPORTUNITY and finding.tier != "quick_win":
        return base + 1
    return base


def order_findings(findings: Sequence[Finding]) -> list[Finding]:
    """De-duplicate by id, sort into sections, assign display ids."""
    unique = {f.finding_id: f for f in findings}

    def key(finding: Finding) -> tuple[int, bool, object, int, str]:
        amount = finding.amount()
        return (_section(finding), amount is None, -abs(amount) if amount is not None else 0,
                -finding.confidence.rank, finding.finding_id)

    ordered = sorted(unique.values(), key=key)
    return [f.model_copy(update={"display_id": f"F-{i:03d}"})
            for i, f in enumerate(ordered, start=1)]


def build_findings(ce_snapshots: Sequence[RawSnapshot], coh_snapshots: Sequence[RawSnapshot],
                   index: ResourceIndex, ruleset: FindingsRuleset,
                   workload_tag_keys: Sequence[str], linked_accounts: Sequence[str],
                   as_of: date) -> FindingsBundle:
    """Every finding for one review, ordered, with asks and talking points."""
    facts: dict[str, CostFact] = {}
    notes: set[str] = set()
    tag_keys: set[str] = set()
    tag_evidence: dict[str, Evidence] = {}
    for snapshot in sorted(ce_snapshots, key=lambda s: s.snapshot_id):
        batch = normalise_cost_explorer(snapshot, ruleset.metric)
        facts.update({f.fact_id: f for f in batch.facts})
        notes.update(batch.notes)
        tag_keys.update(batch.tag_keys_seen)
        labelled = label_evidence(snapshot).get(TAG_KEYS_SEEN)
        if labelled is not None and labelled[0] in ("ok", "empty"):
            tag_evidence[snapshot.account_id] = labelled[2]
    recommendations: dict[str, Recommendation] = {}
    summaries: dict[str, DedupedSummary] = {}
    for snapshot in sorted(coh_snapshots, key=lambda s: s.snapshot_id):
        coh = normalise_cost_optimization_hub(snapshot)
        recommendations.update({r.recommendation_id: r for r in coh.recommendations})
        summaries.update({s.account_id: s for s in coh.summaries})
        notes.update(coh.notes)

    fact_list = sorted(facts.values(), key=lambda f: f.sort_key())
    units = {f.unit for f in fact_list}
    currency = units.pop() if len(units) == 1 else None
    attribution = attribute(fact_list, index, ruleset, workload_tag_keys, as_of)
    primary = {s.workload_id: s for s in attribution.spend if s.primary}
    savings = savings_findings(
        sorted(recommendations.values(), key=lambda r: r.recommendation_id),
        [summaries[a] for a in sorted(summaries)], index, ruleset, as_of, currency, primary)
    notes.update(attribution.notes)
    notes.update(savings.notes)

    found: list[Finding] = []
    found += source_findings(list(ce_snapshots) + list(coh_snapshots))
    if tag_evidence:
        found += tag_visibility_findings(attribution.declared_tag_keys, tag_keys, tag_evidence)
    found += attribution.findings
    found += spend_findings(fact_list, linked_accounts, as_of, ruleset, attribution.spend)
    found += savings.findings
    found += stale_findings({a: list(e) for a, e in savings.stale.items()},
                            ruleset.stale_recommendation_days)
    ordered = order_findings(found)
    asks = build_asks(ordered, index.workloads)
    return FindingsBundle(
        findings=tuple(ordered), attribution=attribution, savings=savings, asks=tuple(asks),
        talking_points=tuple(build_talking_points(ordered, asks)), facts=tuple(fact_list),
        currency=currency, tag_keys_seen=tuple(sorted(tag_keys)), notes=tuple(sorted(notes)))
