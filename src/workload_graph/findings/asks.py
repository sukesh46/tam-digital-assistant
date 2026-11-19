"""Customer asks and talking points (Section 10.10). Templates only, no model calls.

Status: real
Purpose: turn each gap into a specific request a TAM can make, and give the TAM short talking
    points that cite the top finding of each kind.
Inputs: the sorted findings and the counted workloads.
Outputs: ``Ask``, ``TalkingPoint``, ``build_asks``, ``build_talking_points``.
Planned dependencies: cost.money, findings.models.
Notes: an ask lists the finding ids that triggered it. A talking point exists only when its
    source finding exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from workload_graph.cost.money import format_money
from workload_graph.findings.models import Finding, FindingKind
from workload_graph.models import CandidateWorkload

SOURCE_NAMES = {"cost_explorer": "Cost Explorer",
                "cost_optimization_hub": "Cost Optimization Hub"}


class Ask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ask_id: str
    text: str
    finding_ids: tuple[str, ...]


class TalkingPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    point_id: str
    text: str
    finding_ids: tuple[str, ...]


def _ids(findings: Sequence[Finding]) -> tuple[str, ...]:
    return tuple(sorted({f.finding_id for f in findings}))


def _rule(findings: Sequence[Finding], *rules: str) -> list[Finding]:
    return [f for f in findings if f.kind is FindingKind.DATA_QUALITY and f.rule_id in rules]


def build_asks(findings: Sequence[Finding],
               workloads: Mapping[str, CandidateWorkload]) -> list[Ask]:
    """Every ask whose trigger is present, in a fixed order."""
    asks: list[Ask] = []
    resource = _rule(findings, "resource_level_not_enabled", "attribution_low_explained_ratio")
    if resource:
        services = sorted({s for f in resource if f.rule_id == "resource_level_not_enabled"
                           for s in str(f.values.get("services") or "").split(",") if s})
        named = ", ".join(services) if services else "the services with the highest spend"
        asks.append(Ask(ask_id="A-RESOURCE-LEVEL", finding_ids=_ids(resource), text=(
            f"Enable resource-level data in Cost Explorer for {named} so spend can be assigned "
            "to workloads.")))
    for finding in sorted(_rule(findings, "tag_cost_not_visible"),
                          key=lambda f: str(f.values.get("tag_key"))):
        key = str(finding.values.get("tag_key"))
        asks.append(Ask(ask_id=f"A-TAG-ACTIVATE:{key}", finding_ids=(finding.finding_id,), text=(
            f"Activate the tag key '{key}' as a cost allocation tag, and apply it to untagged "
            "resources.")))
    unavailable = _rule(findings, "source_unavailable")
    optin = [f for f in unavailable if f.values.get("source") == "cost_optimization_hub"
             and f.values.get("status") == "not_enabled"]
    if optin:
        asks.append(Ask(ask_id="A-COH-OPTIN", finding_ids=_ids(optin),
                        text="Opt in to Cost Optimization Hub for the accounts in scope."))
    for source in sorted(SOURCE_NAMES):
        denied = [f for f in unavailable if f.values.get("source") == source
                  and f.values.get("status") == "denied"]
        if denied:
            asks.append(Ask(ask_id=f"A-ACCESS:{source}", finding_ids=_ids(denied),
                            text=f"Grant the read-only role access to {SOURCE_NAMES[source]}."))
    low = sorted((w for w in workloads.values() if w.confidence == "low"),
                 key=lambda w: w.workload_id)
    ambiguous = [f for f in findings if "resource_id_ambiguous" in f.flags]
    if low or ambiguous:
        names = ", ".join(w.name_hint for w in low) if low else "the workloads that hold the " \
            "resources flagged as ambiguous"
        related = [f for f in findings if f.workload_id in {w.workload_id for w in low}]
        asks.append(Ask(ask_id="A-CONFIRM-WORKLOADS", finding_ids=_ids(related + ambiguous),
                        text=f"Confirm the boundaries of these workloads: {names}."))
    return asks


def build_talking_points(findings: Sequence[Finding], asks: Sequence[Ask]) -> list[TalkingPoint]:
    """Talking points citing the first (top-ranked) finding of each kind."""
    points: list[TalkingPoint] = []
    driver = next((f for f in findings if f.kind is FindingKind.SPEND_DRIVER), None)
    amount = None if driver is None else driver.amount()
    if driver is not None and amount is not None:
        direction = "rose" if amount > 0 else "fell"
        points.append(TalkingPoint(point_id="T-DRIVER", finding_ids=(driver.finding_id,), text=(
            f"{driver.values.get('service')} spend {direction} by "
            f"{format_money(abs(amount), driver.unit())} month over month.")))
    quick = next((f for f in findings if f.kind is FindingKind.SAVINGS_OPPORTUNITY
                  and f.tier == "quick_win"), None)
    amount = None if quick is None else quick.amount()
    if quick is not None and amount is not None:
        points.append(TalkingPoint(point_id="T-QUICKWIN", finding_ids=(quick.finding_id,), text=(
            f"A low-effort change ({quick.values.get('action_type')}) is estimated to save "
            f"{format_money(amount, quick.unit())} a month.")))
    gap = next((f for f in findings if f.kind is FindingKind.DATA_QUALITY), None)
    if gap is not None:
        points.append(TalkingPoint(point_id="T-GAP", finding_ids=(gap.finding_id,), text=(
            f"Part of the picture is missing ({gap.rule_id.replace('_', ' ')}); the asks list "
            "what would close the gap.")))
    confirm = next((a for a in asks if a.ask_id == "A-CONFIRM-WORKLOADS"), None)
    if confirm is not None and confirm.finding_ids:
        points.append(TalkingPoint(point_id="T-CONFIRM", finding_ids=confirm.finding_ids, text=(
            "Some workload boundaries are uncertain; confirming them sharpens the figures.")))
    return points
