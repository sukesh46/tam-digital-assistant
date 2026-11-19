"""Narrators: the template narrator (real), and the stub for a later model-written narrative.

Status: partial
Purpose: phrase already-computed findings as short sentences. A narrator decides nothing.
Inputs: ``ReportCore`` (the report without its narrative).
Outputs: ``Narrator`` protocol, ``TemplateNarrator``, ``BedrockNarrator`` (stub).
Planned dependencies: cost.money, report.models.
Notes:
    - Every sentence cites the findings it states. Numbers are rendered with the shared
      formatters, so the guardrail accepts them.
    - Workload names are written in double quotes after the word "workload"; the guardrail uses
      that convention to check names (G5).
    - # SKELETON: ``BedrockNarrator`` is an interface only; the model step is not approved.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from workload_graph.cost.money import format_money, format_pct
from workload_graph.findings.models import Finding, FindingKind
from workload_graph.report.models import (
    SECTION_NAMES,
    Narrative,
    NarrativeSection,
    ReportCore,
    Sentence,
)


class Narrator(Protocol):
    def narrate(self, core: ReportCore) -> Narrative: ...


def _money(finding: Finding, key: str) -> str:
    raw = finding.values.get(key)
    return format_money(Decimal(str(raw)), finding.unit())


def _pct(finding: Finding, key: str) -> str:
    raw = finding.values.get(key)
    return format_pct(None if raw is None else Decimal(str(raw)))


def _change(finding: Finding) -> str:
    amount = finding.amount()
    direction = "rose" if amount is not None and amount > 0 else \
        "fell" if amount is not None and amount < 0 else "was unchanged"
    pct = finding.values.get("pct")
    pct_text = f" ({_pct(finding, 'pct')})" if pct is not None else ""
    return f"{direction} from {_money(finding, 'prior')} to {_money(finding, 'current')}{pct_text}"


class TemplateNarrator:
    """Fixed sentence templates per finding kind. Deterministic; always passes the guardrail."""

    def narrate(self, core: ReportCore) -> Narrative:
        by_kind: dict[FindingKind, list[Finding]] = {}
        for finding in core.findings:
            by_kind.setdefault(finding.kind, []).append(finding)
        names = {w.workload_id: w.name_hint for w in core.workloads}
        sections: dict[str, list[Sentence]] = {name: [] for name in SECTION_NAMES}

        changes = [f for f in by_kind.get(FindingKind.SPEND_CHANGE, [])
                   if f.rule_id == "spend_change"]
        headline = next((f for f in changes if f.account_id is None), None) or \
            (changes[0] if changes else None)
        if headline is not None:
            scope = "Total spend" if headline.account_id is None else "Spend"
            sections["summary"].append(Sentence(
                text=f"{scope} {_change(headline)} month over month.",
                finding_ids=(headline.finding_id,)))
        quick = [f for f in by_kind.get(FindingKind.SAVINGS_OPPORTUNITY, [])
                 if f.tier == "quick_win"]
        if quick:
            sections["summary"].append(Sentence(
                text="Low-effort savings opportunities were found; the largest is "
                     f"{_money(quick[0], 'amount')} a month.",
                finding_ids=(quick[0].finding_id,)))

        for finding in changes:
            if finding is headline:
                continue
            sections["account_spend"].append(Sentence(
                text=f"Spend {_change(finding)} month over month.",
                finding_ids=(finding.finding_id,)))
        for finding in by_kind.get(FindingKind.SPEND_CHANGE, []):
            if finding.rule_id == "spend_change_partial_month":
                sections["account_spend"].append(Sentence(
                    text=f"The current month so far is {_money(finding, 'amount')} (estimated).",
                    finding_ids=(finding.finding_id,)))
        for finding in by_kind.get(FindingKind.SPEND_DRIVER, []):
            sections["account_spend"].append(Sentence(
                text=f"{finding.values.get('service')} {_change(finding)}.",
                finding_ids=(finding.finding_id,)))

        for finding in by_kind.get(FindingKind.WORKLOAD_SPEND, []):
            name = names.get(finding.workload_id or "", "")
            sections["workloads"].append(Sentence(
                text=f'Tagged spend for workload "{name}" {_change(finding)}.',
                finding_ids=(finding.finding_id,)))
        for finding in by_kind.get(FindingKind.SAVINGS_OPPORTUNITY, []):
            where = f'in workload "{names[finding.workload_id]}"' if \
                finding.workload_id in names else "at account level"
            tier = "a quick win" if finding.tier == "quick_win" else "to plan first"
            sections["savings"].append(Sentence(
                text=f"{finding.values.get('action_type')} {where} is estimated to save "
                     f"{_money(finding, 'amount')} a month ({tier}).",
                finding_ids=(finding.finding_id,)))
        for finding in by_kind.get(FindingKind.COMMITMENT_OPPORTUNITY, []):
            sections["savings"].append(Sentence(
                text=f"{finding.values.get('action_type')} at account level is estimated to save "
                     f"{_money(finding, 'amount')} a month.",
                finding_ids=(finding.finding_id,)))
        for finding in by_kind.get(FindingKind.DATA_QUALITY, []):
            if finding.rule_id == "savings_overlap":
                text = (f"Recommendations add up to {_money(finding, 'sum_of_items')} but the "
                        f"deduplicated total is {_money(finding, 'deduplicated_total')}; do not "
                        "add these figures.")
            else:
                text = f"Data gap: {finding.rule_id.replace('_', ' ')}."
            sections["data_coverage"].append(Sentence(text=text,
                                                      finding_ids=(finding.finding_id,)))
        for ask in core.customer_asks:
            if ask.finding_ids:
                sections["asks"].append(Sentence(text=ask.text, finding_ids=ask.finding_ids))

        return Narrative(sections=tuple(NarrativeSection(name=n, sentences=tuple(sections[n]))
                                        for n in SECTION_NAMES))


class BedrockNarrator:
    """# SKELETON: interface for a model-written narrative. Not wired; the step is not approved."""

    def narrate(self, core: ReportCore) -> Narrative:
        raise NotImplementedError("SKELETON: LLM narrative step not approved")
